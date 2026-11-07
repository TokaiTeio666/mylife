"""FX-13 搜索性能基准（10k 文档 / 80k chunk / 50 查询）。

用法（需要 PostgreSQL 与已迁移的 mylife 库）：
  MYLIFE_DATABASE_URL=postgresql+psycopg://mylife_app:change-me-app@localhost:25432/mylife \
  uv run python scripts/bench_search.py --docs 10000 --chunks-per-doc 8

产出：docs/evidence/fx13-search.json（P50/P95/P99、冷启动排除标记）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("MYLIFE_EMBEDDING_PROVIDER", "mock")
os.environ.setdefault(
    "MYLIFE_DATABASE_URL",
    "postgresql+psycopg://mylife_app:change-me-app@localhost:25432/mylife",
)
os.environ.setdefault("MYLIFE_RATE_LIMIT_BACKEND", "memory")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import psycopg  # noqa: E402
from sqlalchemy import text  # noqa: E402

from mylife_api.db import get_session_factory, set_rls_context  # noqa: E402
from mylife_api.services_search import search  # noqa: E402
from mylife_retrieval.tokenizer import build_search_text  # noqa: E402

ADMIN_DSN = os.environ.get(
    "MYLIFE_BENCH_ADMIN_DSN",
    "postgresql://mylife_migrator:change-me-migrator@localhost:25432/mylife",
)
BENCH_SLUG = "bench-search"

TERMS = "缓存穿透 布隆过滤器 Redis MySQL 数据库索引 Python 异步 复盘 方法论 学习计划 事务"
# 与 ingest 一致：写库前先 jieba 分词为空格串，否则 tsv 会把整个中文短语当作一个 token。
SEARCH_TEXT = build_search_text(TERMS)

QUERIES: list[dict[str, object]] = [
    {"query": "缓存穿透"},
    {"query": "布隆过滤器"},
    {"query": "Redis 缓存"},
    {"query": "MySQL 索引"},
    {"query": "Python 异步"},
    {"query": "复盘 方法论"},
    {"query": "学习计划"},
    {"query": "数据库 事务"},
    {"query": "缓存穿透", "path_prefix": "notes/"},
    {"query": "缓存穿透", "tags": ["bench"]},
    {"query": "缓存穿透", "extensions": ["md"]},
    {"query": "缓存穿透", "mode": "lexical"},
    {"query": "缓存穿透", "mode": "vector"},
] * 4


def _vector_literal(dimension: int = 1024) -> str:
    value = 1.0 / (dimension**0.5)
    return "[" + ",".join(f"{value:.8f}" for _ in range(dimension)) + "]"


def prepare(admin: psycopg.Connection, docs: int, chunks_per_doc: int) -> tuple[str, str]:
    admin.execute("DELETE FROM workspaces WHERE slug = %s", (BENCH_SLUG,))
    user_id = admin.execute(
        "INSERT INTO users (email, password_hash, display_name) "
        "VALUES ('bench@example.com', 'x', 'bench') "
        "ON CONFLICT (lower(email)) DO UPDATE SET updated_at = now() RETURNING id"
    ).fetchone()[0]
    workspace_id = admin.execute(
        "INSERT INTO workspaces (name, slug, owner_id) VALUES ('Bench', %s, %s) RETURNING id",
        (BENCH_SLUG, user_id),
    ).fetchone()[0]

    print(f"seeding {docs} documents ...", flush=True)
    admin.execute(
        """
        INSERT INTO documents (
          id, workspace_id, path, title, content, content_hash, raw_hash, size,
          mtime, indexed_at, tags, extension, search_text, occurred_on, source_type
        )
        SELECT gen_random_uuid(), %s, 'notes/doc-' || i || '.md', '笔记 ' || i,
               %s, md5(i::text) || md5('h' || i::text), md5('r' || i::text),
               4096, now(), now(), ARRAY['bench','缓存'], 'md',
               %s, date '2026-01-01' + (i %% 365), 'user'
        FROM generate_series(1, %s) AS i
        """,
        (workspace_id, f"{TERMS} 正文内容", SEARCH_TEXT, docs),
    )

    print(f"seeding {docs * chunks_per_doc} chunks ...", flush=True)
    admin.execute(
        """
        INSERT INTO document_chunks (
          id, workspace_id, document_id, heading, content, token_count,
          chunk_ordinal, chunk_hash, line_start, line_end, embedding
        )
        SELECT gen_random_uuid(), d.workspace_id, d.id, '章节 ' || c,
               %s, 32, c, md5(d.id::text || c::text) || md5('c' || c::text),
               c * 10, c * 10 + 9,
               CAST((
                 SELECT array_agg(random()::real)
                 FROM generate_series(
                   1, 1024 + (hashtext(d.id::text || c::text)::int * 0)
                 )
               ) AS vector)
        FROM documents d CROSS JOIN generate_series(1, %s) AS c
        WHERE d.workspace_id = %s
        """,
        (f"{TERMS} 章节正文", chunks_per_doc, workspace_id),
    )
    admin.execute("ANALYZE documents")
    admin.execute("ANALYZE document_chunks")
    return str(workspace_id), str(user_id)


async def run_benchmark(workspace_id: str, user_id: str, queries: list[dict]) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        await set_rls_context(session, workspace_id, user_id)
        timings: list[float] = []
        degraded = 0
        warmups = 5
        for index, params in enumerate(queries):
            started = time.perf_counter()
            response = await search(
                session,
                workspace_id=workspace_id,
                query=str(params["query"]),
                top_k=20,
                mode=str(params.get("mode", "hybrid")),
                source_type="document",
                path_prefix=params.get("path_prefix"),  # type: ignore[arg-type]
                tags=params.get("tags"),  # type: ignore[arg-type]
                date_from=None,
                date_to=None,
                extensions=params.get("extensions"),  # type: ignore[arg-type]
                include_snippet_html=True,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            if index >= warmups:
                timings.append(elapsed_ms)
            if response.degraded:
                degraded += 1
            if params.get("mode") != "vector":
                assert response.items, f"无结果: {params}"
        await session.rollback()
    timings.sort()
    return {
        "samples": len(timings),
        "degraded_responses": degraded,
        "p50_ms": round(statistics.median(timings), 2),
        "p95_ms": round(timings[int(len(timings) * 0.95) - 1], 2),
        "p99_ms": round(timings[int(len(timings) * 0.99) - 1], 2),
        "max_ms": round(max(timings), 2),
        "warmups_excluded": warmups,
        "p95_pass": timings[int(len(timings) * 0.95) - 1] < 1000,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=10_000)
    parser.add_argument("--chunks-per-doc", type=int, default=8)
    parser.add_argument("--queries", type=int, default=len(QUERIES))
    args = parser.parse_args()

    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        workspace_id, user_id = prepare(admin, args.docs, args.chunks_per_doc)

    queries = (QUERIES * 10)[: args.queries]
    report = asyncio.run(run_benchmark(workspace_id, user_id, queries))
    report.update(
        {
            "docs": args.docs,
            "chunks": args.docs * args.chunks_per_doc,
            "dataset_note": "synthetic; same terms in every document",
        }
    )

    output = Path("docs/evidence")
    output.mkdir(parents=True, exist_ok=True)
    target = output / "fx13-search.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["p95_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
