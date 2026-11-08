"""混合检索（ARCHITECTURE §2.6；AC-16…AC-20）。

三路候选：tsv 词法 top50、pgvector 余弦 top50、标题/路径 top20；按 chunk_id 去重后
用确定性 RRF（k=60）融合，排序键 `(score desc, path asc, chunk_ordinal asc)`。
"""

from __future__ import annotations

import asyncio
import time
from datetime import date
from typing import Any
from uuid import UUID

from mylife_contract.errors import ErrorCode
from mylife_contract.search import SearchResponse, SearchResult, SuggestItem, SuggestResponse
from mylife_retrieval.embedding import EmbeddingUnavailable, build_provider
from mylife_retrieval.rrf import rrf_fuse, sort_key
from mylife_retrieval.snippet import make_snippet
from mylife_retrieval.tokenizer import build_tsquery, tokenize_for_search
from psycopg.errors import QueryCanceled
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api.errors import AppError
from mylife_api.settings import settings

VECTOR_TIMEOUT_SECONDS = 0.25
LEXICAL_LIMIT = 50
VECTOR_LIMIT = 50
TITLE_LIMIT = 20


def _filters(
    alias: str,
    *,
    path_prefix: str | None,
    tags: list[str] | None,
    date_from: date | None,
    date_to: date | None,
    extensions: list[str] | None,
) -> tuple[list[str], dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if path_prefix:
        clauses.append(f"{alias}.path LIKE :path_prefix")
        params["path_prefix"] = f"{path_prefix.rstrip('/')}%"
    if tags:
        clauses.append(f"{alias}.tags && :tags")
        params["tags"] = tags
    if date_from is not None:
        clauses.append(f"{alias}.occurred_on >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        clauses.append(f"{alias}.occurred_on <= :date_to")
        params["date_to"] = date_to
    if extensions:
        clauses.append(f"{alias}.extension = ANY(:extensions)")
        params["extensions"] = extensions
    return clauses, params


async def _lexical_route(
    session: AsyncSession,
    workspace_id: UUID,
    q: str,
    where: list[str],
    params: dict[str, Any],
    table: str = "document_chunks",
) -> list[dict[str, Any]]:
    clauses = " AND ".join(["d.workspace_id = :ws", "d.tsv @@ q.query", *where])
    query = text(
        f"""
        WITH q AS (SELECT to_tsquery('simple', :tsq) AS query),
        matched AS (
          SELECT d.id, d.path, ts_rank_cd(d.tsv, q.query) AS rank
          FROM documents d, q
          WHERE {clauses}
          ORDER BY rank DESC, d.path ASC
          LIMIT {LEXICAL_LIMIT}
        )
        SELECT c.id AS chunk_id, c.document_id, c.heading, c.content,
               c.line_start, c.line_end, c.chunk_ordinal,
               d.path, d.title, d.occurred_on, m.rank
        FROM matched m
        JOIN documents d ON d.id = m.id
        JOIN LATERAL (
          SELECT c.* FROM {table} c
          WHERE c.document_id = m.id
          ORDER BY (to_tsvector('simple', c.content) @@ (SELECT query FROM q)) DESC,
                   c.chunk_ordinal ASC
          LIMIT 1
        ) c ON TRUE
        ORDER BY m.rank DESC, d.path ASC
        """
    )
    result = await session.execute(query, {**params, "ws": workspace_id, "tsq": q})
    return [dict(r._mapping) for r in result]


async def _vector_route(
    session: AsyncSession,
    workspace_id: UUID,
    vector_literal: str,
    where: list[str],
    params: dict[str, Any],
    table: str = "document_chunks",
) -> list[dict[str, Any]]:
    clauses = " AND ".join(
        ["c.workspace_id = :ws", "c.embedding IS NOT NULL", *where]
    )
    # 内层只按距离排序，HNSW 才可能被使用；附加排序键放到外层小结果集上。
    query = text(
        f"""
        WITH candidates AS (
          SELECT c.id, c.embedding <=> CAST(:vector AS vector) AS distance
          FROM {table} c
          JOIN documents d ON d.id = c.document_id
          WHERE {clauses}
          ORDER BY c.embedding <=> CAST(:vector AS vector)
          LIMIT {VECTOR_LIMIT}
        )
        SELECT c.id AS chunk_id, c.document_id, c.heading, c.content,
               c.line_start, c.line_end, c.chunk_ordinal,
               d.path, d.title, d.occurred_on, cand.distance
        FROM candidates cand
        JOIN {table} c ON c.id = cand.id
        JOIN documents d ON d.id = c.document_id
        ORDER BY cand.distance ASC, d.path ASC, c.chunk_ordinal ASC
        """
    )
    result = await session.execute(query, {**params, "ws": workspace_id, "vector": vector_literal})
    return [dict(r._mapping) for r in result]


async def _title_route(
    session: AsyncSession,
    workspace_id: UUID,
    like: str,
    where: list[str],
    params: dict[str, Any],
    table: str = "document_chunks",
) -> list[dict[str, Any]]:
    clauses = " AND ".join(
        ["d.workspace_id = :ws", "(d.title ILIKE :like OR d.path ILIKE :like)", *where]
    )
    query = text(
        f"""
        SELECT c.id AS chunk_id, c.document_id, c.heading, c.content,
               c.line_start, c.line_end, c.chunk_ordinal,
               d.path, d.title, d.occurred_on
        FROM documents d
        JOIN LATERAL (
          SELECT c.* FROM {table} c WHERE c.document_id = d.id
          ORDER BY c.chunk_ordinal ASC LIMIT 1
        ) c ON TRUE
        WHERE {clauses}
        ORDER BY d.path ASC
        LIMIT {TITLE_LIMIT}
        """
    )
    result = await session.execute(query, {**params, "ws": workspace_id, "like": like})
    return [dict(r._mapping) for r in result]


async def _hydrate(
    session: AsyncSession, chunk_ids: list[UUID], table: str = "document_chunks"
) -> dict[UUID, dict[str, Any]]:
    if not chunk_ids:
        return {}
    result = await session.execute(
        text(
            "SELECT c.id AS chunk_id, c.document_id, c.heading, c.content, c.line_start, "
            "       c.line_end, c.chunk_ordinal, d.path, d.title, d.occurred_on "
            f"FROM {table} c JOIN documents d ON d.id = c.document_id "
            "WHERE c.id = ANY(:ids)"
        ),
        {"ids": chunk_ids},
    )
    return {row._mapping["chunk_id"]: dict(row._mapping) for row in result}


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


def _result_model(
    row: dict[str, Any],
    *,
    score: float,
    matched_by: str,
    rank_lexical: int | None,
    rank_vector: int | None,
    query: str,
    include_snippet_html: bool,
) -> SearchResult:
    snippet, snippet_html = make_snippet(row["content"], query)
    line_range = None
    if row.get("line_start") is not None and row.get("line_end") is not None:
        line_range = [int(row["line_start"]), int(row["line_end"])]
    return SearchResult(
        source_type="document",
        document_id=row["document_id"],
        chunk_id=row["chunk_id"],
        review_id=None,
        path=row["path"],
        title=row["title"],
        heading=row.get("heading"),
        snippet=snippet,
        snippet_html=snippet_html if include_snippet_html else None,
        score=score,
        matched_by=matched_by,  # type: ignore[arg-type]
        rank_lexical=rank_lexical,
        rank_vector=rank_vector,
        line_range=line_range,
        occurred_on=row.get("occurred_on"),
    )


async def search(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    query: str,
    top_k: int,
    mode: str,
    source_type: str,
    path_prefix: str | None,
    tags: list[str] | None,
    date_from: date | None,
    date_to: date | None,
    extensions: list[str] | None,
    include_snippet_html: bool = True,
) -> SearchResponse:
    started = time.perf_counter()
    query = (query or "").strip()
    if not query:
        raise AppError(ErrorCode.SEARCH_EMPTY_QUERY)
    tokens = tokenize_for_search(query)
    if len(tokens) > 64:
        raise AppError(ErrorCode.SEARCH_TOO_MANY_TERMS)
    if source_type == "review":
        items = await _review_route(
            session,
            workspace_id=workspace_id,
            query=query,
            top_k=top_k,
            path_prefix=path_prefix,
            date_from=date_from,
            date_to=date_to,
        )
        return SearchResponse(
            items=items,
            total_estimated=len(items),
            degraded=False,
            degraded_reasons=[],
            took_ms=int((time.perf_counter() - started) * 1000),
        )

    where, params = _filters(
        "d",
        path_prefix=path_prefix,
        tags=tags,
        date_from=date_from,
        date_to=date_to,
        extensions=extensions,
    )

    routes: list[tuple[str, list[UUID]]] = []
    degraded_reasons: list[str] = []

    # 蓝绿换维后向量可能在 document_chunks_v{n}（PHASE5-SPEC D8）。
    from mylife_api import services_embedding

    table = await services_embedding.chunk_table(session, workspace_id=workspace_id)

    if mode in {"hybrid", "lexical"}:
        ts_query, _ = build_tsquery(query)
        if ts_query:
            lexical = await _lexical_route(
                session, workspace_id, ts_query, where, params, table=table
            )
            routes.append(("lexical", [row["chunk_id"] for row in lexical]))

    if mode in {"hybrid", "vector"}:
        try:
            # 查询向量必须与当前表维度一致：用 active profile 的模型/维度，
            # 否则 building 期间会用新维度去查旧表（PHASE5-SPEC 风险 5）。
            params_for_profile = await services_embedding.active_embedding_params(
                session, workspace_id=workspace_id
            )
            provider = build_provider(
                provider=settings.embedding_provider,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                model=params_for_profile["model"],
                dimension=params_for_profile["dimension"],
            )
            # 只对 HTTP embedding 使用 wait_for；数据库查询用 statement_timeout，
            # 避免取消 in-flight 查询把连接/事务置于不可用状态。
            vectors = await asyncio.wait_for(
                provider.embed([query]), timeout=VECTOR_TIMEOUT_SECONDS
            )
            try:
                async with session.begin_nested():
                    await session.execute(
                        text("SET LOCAL statement_timeout = '250ms'")
                    )
                    # 防止 prepared statement 的 generic plan 放弃 HNSW 索引。
                    await session.execute(
                        text("SET LOCAL plan_cache_mode = force_custom_plan")
                    )
                    vector_rows = await _vector_route(
                        session,
                        workspace_id,
                        _vector_literal(vectors[0]),
                        where,
                        params,
                        table=table,
                    )
            except DBAPIError as exc:
                if isinstance(getattr(exc, "orig", None), QueryCanceled):
                    degraded_reasons.append("vector_timeout")
                    vector_rows = []
                else:
                    raise
            finally:
                # SET LOCAL 在事务内持续生效；释放 savepoint 后必须复位，
                # 否则后续词法查询会被 250ms 的 statement_timeout 误杀。
                await session.execute(text("SET LOCAL statement_timeout = 0"))
                await session.execute(text("SET LOCAL plan_cache_mode = auto"))
            routes.append(("vector", [row["chunk_id"] for row in vector_rows]))
        except TimeoutError:
            degraded_reasons.append("vector_timeout")
        except EmbeddingUnavailable:
            degraded_reasons.append("embedding_unavailable")

    like = f"%{query.replace('%', '\\%').replace('_', '\\_')}%"
    title_rows = await _title_route(
        session, workspace_id, like, where, params, table=table
    )
    routes.append(("title", [row["chunk_id"] for row in title_rows]))

    fused = rrf_fuse(routes)
    ordered = sorted(fused.values(), key=lambda item: sort_key(item, "", 0))

    hydrated = await _hydrate(session, [item.key for item in ordered], table=table)
    results: list[tuple[tuple[float, str, int], SearchResult]] = []
    for item in ordered:
        row = hydrated.get(item.key)
        if row is None:
            continue
        matched = item.matched_by
        if "vector" in matched and "lexical" in matched:
            matched_by = "both"
        elif "vector" in matched:
            matched_by = "vector"
        else:
            matched_by = "lexical"
        result = _result_model(
            row,
            score=item.score,
            matched_by=matched_by,
            rank_lexical=item.rank_lexical,
            rank_vector=item.rank_vector,
            query=query,
            include_snippet_html=include_snippet_html,
        )
        results.append(
            (
                (-item.score, row["path"], int(row.get("chunk_ordinal") or 0)),
                result,
            )
        )

    results.sort(key=lambda pair: pair[0])
    items = [result for _, result in results[:top_k]]

    if source_type == "both":
        # 复盘与笔记独立索引；按归一化分数合并（AC-48）。
        review_items = await _review_route(
            session,
            workspace_id=workspace_id,
            query=query,
            top_k=top_k,
            path_prefix=path_prefix,
            date_from=date_from,
            date_to=date_to,
        )
        merged = [(item.score, item.path, item) for item in items]
        merged.extend((item.score, item.path, item) for item in review_items)
        merged.sort(key=lambda row: (-row[0], row[1]))
        items = [row[2] for row in merged[:top_k]]

    took_ms = int((time.perf_counter() - started) * 1000)
    return SearchResponse(
        items=items,
        total_estimated=len(fused) + (len(items) if source_type == "both" else 0),
        degraded=bool(degraded_reasons),
        degraded_reasons=degraded_reasons,
        took_ms=took_ms,
    )


def _review_result(row: dict[str, Any], *, query: str, rank: float) -> SearchResult:
    text_value = row.get("search_text") or ""
    snippet = text_value[:200]
    # 片段来自用户笔记：必须先转义再插入 <mark>，否则前端
    # dangerouslySetInnerHTML 会执行笔记中的 HTML（SEC-20）。
    import html

    escaped = html.escape(snippet)
    marked = escaped.replace(html.escape(query), f"<mark>{html.escape(query)}</mark>")
    return SearchResult(
        source_type="review",
        document_id=None,
        chunk_id=None,
        review_id=row["review_id"],
        path=row["path"],
        title=row["title"],
        heading=None,
        snippet=snippet,
        snippet_html=marked,
        score=round(float(rank), 6),
        matched_by="lexical",
        rank_lexical=None,
        rank_vector=None,
        line_range=None,
        occurred_on=row.get("occurred_on"),
    )


async def _review_route(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    query: str,
    top_k: int,
    path_prefix: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[SearchResult]:
    """`review_search` 词法检索（AC-48）；只返回 current 版本。"""
    from mylife_retrieval.tokenizer import build_tsquery

    from mylife_api.repositories import get_settings

    ts_query, _ = build_tsquery(query)
    conditions = ["rs.workspace_id = :ws", "rs.is_current"]
    params: dict[str, Any] = {"ws": workspace_id, "limit": top_k}
    # `to_tsvector('simple', ...)` 不切分中文；中文查询走 ILIKE 子串匹配。
    if ts_query:
        conditions.append(
            "(rs.tsv @@ to_tsquery('simple', :tsq) OR rs.search_text ILIKE :like)"
        )
        params["tsq"] = ts_query
        params["like"] = f"%{query}%"
    else:
        conditions.append("rs.search_text ILIKE :like")
        params["like"] = f"%{query}%"
    if path_prefix:
        prefix = path_prefix.rstrip("/")
        if prefix.startswith("data/reviews"):
            settings_row = await get_settings(session, workspace_id) or {}
            root = settings_row.get("reviews_root") or "wiki/复盘"
            prefix = f"{root.rstrip('/')}/{prefix[len('data/reviews'):].lstrip('/')}".rstrip("/")
        conditions.append("rs.path LIKE :prefix")
        params["prefix"] = f"{prefix}%"
    if date_from is not None:
        conditions.append("rs.occurred_on >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        conditions.append("rs.occurred_on <= :date_to")
        params["date_to"] = date_to
    rank_sql = (
        "ts_rank(rs.tsv, to_tsquery('simple', :tsq))"
        if ts_query
        else "CASE WHEN rs.title ILIKE :like THEN 1.0 ELSE 0.5 END"
    )
    result = await session.execute(
        text(
            "SELECT rs.review_id, rs.path, rs.title, rs.search_text, rs.occurred_on, "
            f"       {rank_sql} AS rank "
            "FROM review_search rs WHERE "
            + " AND ".join(conditions)
            + " ORDER BY rank DESC, rs.path ASC LIMIT :limit"
        ),
        params,
    )
    return [
        _review_result(dict(row._mapping), query=query, rank=row.rank)
        for row in result
    ]


async def suggest(
    session: AsyncSession, *, workspace_id: UUID, query: str, limit: int = 5
) -> SuggestResponse:
    query = (query or "").strip()
    if not query:
        raise AppError(ErrorCode.SEARCH_EMPTY_QUERY)
    like = f"%{query.replace('%', '\\%').replace('_', '\\_')}%"
    result = await session.execute(
        text(
            "SELECT id, title, path FROM documents WHERE workspace_id = :ws "
            "AND (title ILIKE :like OR path ILIKE :like) "
            "ORDER BY title ASC LIMIT :limit"
        ),
        {"ws": workspace_id, "like": like, "limit": limit},
    )
    return SuggestResponse(
        items=[
            SuggestItem(text=row.title, path=row.path, document_id=row.id) for row in result
        ]
    )
