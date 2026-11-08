"""Embedding 设置与蓝绿换维（API-CONTRACTS §3.9.4–§3.9.6；AC-50 / FX-11）。

流程（PHASE5-SPEC §4.3）：
1. 校验 dimension 与重复 profile；
2. 通过 `create_chunk_table()`（`mylife_migrator` 持有的 SECURITY DEFINER 函数）
   建新表 + HNSW + FORCE RLS——`mylife_app` 本身没有 CREATE 权限；
3. 后台逐 chunk 复制（**保留 id**）并重算 embedding，写 `embedding_reembed_jobs` 进度；
4. 覆盖率达到 1.0 才原子切换 `workspace_settings.embedding_profile_id`；
5. 旧 profile `retired` + `retire_at`，7 天后由 dispatcher 删除（先写快照）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from mylife_contract.chunk_tables import BASE_CHUNK_TABLE, validate_chunk_table
from mylife_contract.constants import EMBEDDING_RETIRE_DAYS
from mylife_contract.errors import ErrorCode
from mylife_contract.settings import (
    EmbeddingProfile,
    EmbeddingProfileCreate,
    EmbeddingProfileCreated,
    EmbeddingSettings,
    EmbeddingTestResult,
    ProviderTestError,
    ReembedProgress,
)
from mylife_retrieval.embedding import EmbeddingUnavailable, build_provider
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories
from mylife_api.crypto import decrypt_credential, encrypt_credential
from mylife_api.errors import AppError
from mylife_api.repositories import insert_audit
from mylife_api.settings import settings as app_settings

logger = logging.getLogger("mylife.api.embedding")

#: 每批 re-embed 的 chunk 数（避免单事务过大）。
REEMBED_BATCH = 200

_tasks: set[asyncio.Task[Any]] = set()


def _profile_model(row: dict[str, Any]) -> EmbeddingProfile:
    return EmbeddingProfile(
        id=row["id"],
        provider=row["provider"],
        model=row["model"],
        dimension=int(row["dimension"]),
        index_name=row["index_name"],
        status=row["status"],
        created_at=row["created_at"],
        coverage=float(row["coverage"]) if row.get("coverage") is not None else 0.0,
        chunk_table=row.get("chunk_table"),
        activated_at=row.get("activated_at"),
        retire_at=row.get("retire_at"),
    )


async def _coverage(session: AsyncSession, *, table: str) -> float:
    """`已 embedding 的行数 / 基表总行数`；基表为空时视为 1.0。"""
    validate_chunk_table(table)
    total = (await session.execute(text("SELECT count(*) FROM document_chunks"))).scalar_one()
    if int(total) == 0:
        return 1.0
    done = (
        await session.execute(
            text(f"SELECT count(*) FROM {table} WHERE embedding IS NOT NULL")
        )
    ).scalar_one()
    return int(done) / int(total)


async def _active_profile_row(
    session: AsyncSession, *, workspace_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT p.* FROM embedding_profiles p "
            "JOIN workspace_settings s ON s.embedding_profile_id = p.id "
            "WHERE s.workspace_id = :ws"
        ),
        {"ws": workspace_id},
    )
    row = result.first()
    return dict(row._mapping) if row else None


async def get_embedding_settings(
    session: AsyncSession, *, workspace_id: UUID
) -> EmbeddingSettings:
    result = await session.execute(
        text(
            "SELECT id, provider, model, dimension, index_name, status, created_at, "
            "       coverage, chunk_table, activated_at, retire_at "
            "FROM embedding_profiles WHERE workspace_id = :ws ORDER BY created_at ASC"
        ),
        {"ws": workspace_id},
    )
    rows = [dict(row._mapping) for row in result]
    active_row = await _active_profile_row(session, workspace_id=workspace_id)
    active = _profile_model(active_row) if active_row is not None else None

    reembed: ReembedProgress | None = None
    job_row = (
        await session.execute(
            text(
                "SELECT id, status, processed_chunks, total_chunks, error "
                "FROM embedding_reembed_jobs WHERE workspace_id = :ws "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"ws": workspace_id},
        )
    ).first()
    if job_row is not None:
        reembed = ReembedProgress(
            job_id=job_row.id,
            status=job_row.status,
            processed_chunks=int(job_row.processed_chunks),
            total_chunks=int(job_row.total_chunks),
            error=job_row.error,
        )

    credential = await repositories.get_provider_credential(
        session, workspace_id=workspace_id, provider="embedding"
    )
    from mylife_api.services_llm import _wire_status

    return EmbeddingSettings(
        active_profile=active,
        profiles=[_profile_model(row) for row in rows],
        credential_status=_wire_status(credential),  # type: ignore[arg-type]
        last_tested_at=(credential or {}).get("last_tested_at"),
        reembed=reembed,
    )


async def test_embedding(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    provider: str,
    model: str,
    api_key: str | None,
) -> EmbeddingTestResult:
    """不落库；返回观测到的维度（API §3.9.6）。"""
    key = api_key
    if key is None:
        row = await repositories.get_provider_credential(
            session, workspace_id=workspace_id, provider="embedding"
        )
        if row is not None:
            key = decrypt_credential(
                bytes(row["key_ciphertext"]),
                workspace_id=str(workspace_id),
                provider="embedding",
            )
    if provider == "mock":
        return EmbeddingTestResult(ok=True, dimension_observed=app_settings.embedding_dimension)
    if not key:
        raise AppError(ErrorCode.PROVIDER_KEY_MISSING)
    try:
        impl = build_provider(
            provider=provider,
            base_url=app_settings.embedding_base_url,
            api_key=key,
            model=model,
            dimension=app_settings.embedding_dimension,
        )
        vectors = await impl.embed(["ping"])
    except EmbeddingUnavailable:
        return EmbeddingTestResult(
            ok=False,
            error=ProviderTestError(code="EMBEDDING_UNAVAILABLE", message="Embedding 不可用"),
        )
    return EmbeddingTestResult(ok=True, dimension_observed=len(vectors[0]))


async def _bind(session: AsyncSession, workspace_id: UUID, user_id: UUID | None) -> None:
    from mylife_api.db import set_rls_context

    await set_rls_context(session, workspace_id, user_id)


async def create_profile(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
    payload: EmbeddingProfileCreate,
) -> EmbeddingProfileCreated:
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    if not payload.confirm:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, details={"field": "confirm", "reason": "REQUIRED"}
        )

    building = (
        await session.execute(
            text(
                "SELECT id FROM embedding_profiles WHERE workspace_id = :ws "
                "AND status = 'building' LIMIT 1"
            ),
            {"ws": workspace_id},
        )
    ).first()
    if building is not None:
        raise AppError(ErrorCode.CONFLICT, details={"reason": "BUILDING_PROFILE_EXISTS"})

    duplicate = (
        await session.execute(
            text(
                "SELECT id FROM embedding_profiles WHERE workspace_id = :ws "
                "AND model = :model AND dimension = :dim LIMIT 1"
            ),
            {"ws": workspace_id, "model": payload.model, "dim": payload.dimension},
        )
    ).first()
    if duplicate is not None:
        raise AppError(ErrorCode.CONFLICT, details={"reason": "PROFILE_EXISTS"})

    # 表名按维度推导；与基表同维度时复用基表，避免无意义的复制。
    table = (
        BASE_CHUNK_TABLE
        if payload.dimension == app_settings.embedding_dimension
        else f"document_chunks_v{payload.dimension}"
    )
    validate_chunk_table(table)
    if table != BASE_CHUNK_TABLE:
        await session.execute(
            text("SELECT create_chunk_table(:table, :dim)"),
            {"table": table, "dim": payload.dimension},
        )

    profile_id = (
        await session.execute(
            text(
                "INSERT INTO embedding_profiles "
                "(workspace_id, provider, model, dimension, index_name, status, "
                " chunk_table, coverage) "
                "VALUES (:ws, :provider, :model, :dim, :index, 'building', :table, 0) "
                "RETURNING id"
            ),
            {
                "ws": workspace_id,
                "provider": payload.provider,
                "model": payload.model,
                "dim": payload.dimension,
                "index": f"idx_{table}_embedding",
                "table": table,
            },
        )
    ).scalar_one()

    if payload.api_key:
        await repositories.upsert_provider_credential(
            session,
            workspace_id=workspace_id,
            provider="embedding",
            key_ciphertext=encrypt_credential(
                payload.api_key, workspace_id=str(workspace_id), provider="embedding"
            ),
            key_version=1,
            model=payload.model,
            provider_name=payload.provider,
            status="active",
        )

    job_id = (
        await session.execute(
            text(
                "INSERT INTO embedding_reembed_jobs "
                "(workspace_id, profile_id, status, total_chunks) "
                "SELECT :ws, :profile, 'queued', count(*) FROM document_chunks "
                "RETURNING id"
            ),
            {"ws": workspace_id, "profile": profile_id},
        )
    ).scalar_one()

    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="settings.embedding.updated",
        target=str(profile_id),
        detail={
            "provider": payload.provider,
            "model": payload.model,
            "dimension": payload.dimension,
            "chunk_table": table,
        },
    )
    await session.commit()
    await _bind(session, workspace_id, user_id)

    row = (
        await session.execute(
            text(
                "SELECT id, provider, model, dimension, index_name, status, created_at, "
                "       coverage, chunk_table, activated_at, retire_at "
                "FROM embedding_profiles WHERE id = :id"
            ),
            {"id": profile_id},
        )
    ).one()
    return EmbeddingProfileCreated(
        profile=_profile_model(dict(row._mapping)), job_id=job_id
    )


async def run_reembed(
    session: AsyncSession, *, workspace_id: UUID, profile_id: UUID, job_id: UUID
) -> dict[str, Any]:
    """全量 re-embed：复制 chunk 行（保留 id）并重算向量。

    幂等：以目标表里是否已有该 id 为准，重复执行不会产生重复行。
    """
    profile = (
        await session.execute(
            text(
                "SELECT id, provider, model, dimension, chunk_table "
                "FROM embedding_profiles WHERE id = :id AND workspace_id = :ws"
            ),
            {"id": profile_id, "ws": workspace_id},
        )
    ).first()
    if profile is None:
        raise AppError(ErrorCode.NOT_FOUND)
    table = validate_chunk_table(str(profile.chunk_table))

    await session.execute(
        text("UPDATE embedding_reembed_jobs SET status = 'running' WHERE id = :id"),
        {"id": job_id},
    )
    await session.commit()
    # `set_config(..., true)` 是事务局部设置：commit 之后 RLS 上下文清空，
    # 后续 SELECT 会因默认拒绝而返回 0 行（表现为“覆盖率为 1.0 但一行没写”）。
    await _bind(session, workspace_id, None)

    credential = await repositories.get_provider_credential(
        session, workspace_id=workspace_id, provider="embedding"
    )
    api_key = (
        decrypt_credential(
            bytes(credential["key_ciphertext"]),
            workspace_id=str(workspace_id),
            provider="embedding",
        )
        if credential is not None
        else app_settings.embedding_api_key
    )
    try:
        embedder = build_provider(
            provider=str(profile.provider),
            base_url=app_settings.embedding_base_url,
            api_key=api_key,
            model=str(profile.model),
            dimension=int(profile.dimension),
        )
    except EmbeddingUnavailable:
        await _fail_job(session, job_id, 0, "EMBEDDING_UNAVAILABLE")
        return {"status": "failed", "reason": "EMBEDDING_UNAVAILABLE"}

    processed = 0
    while True:
        rows = (
            await session.execute(
                text(
                    "SELECT c.id, c.workspace_id, c.document_id, c.heading, c.content, "
                    "       c.token_count, c.chunk_ordinal, c.chunk_hash, "
                    "       c.line_start, c.line_end "
                    "FROM document_chunks c "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.id = c.id) "
                    "ORDER BY c.chunk_ordinal, c.id LIMIT :limit"
                ),
                {"limit": REEMBED_BATCH},
            )
        ).all()
        if not rows:
            break
        try:
            vectors = await embedder.embed([row.content for row in rows])
        except EmbeddingUnavailable:
            await _fail_job(session, job_id, processed, "EMBEDDING_UNAVAILABLE")
            return {"status": "failed", "reason": "EMBEDDING_UNAVAILABLE"}

        await session.execute(
            text(
                f"INSERT INTO {table} "
                "(id, workspace_id, document_id, heading, content, token_count, "
                " chunk_ordinal, chunk_hash, embedding, embedding_profile_id, "
                " line_start, line_end) "
                "VALUES (:id, :ws, :doc, :heading, :content, :tokens, :ordinal, :hash, "
                "        CAST(:vector AS vector), :profile, :line_start, :line_end) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  embedding = EXCLUDED.embedding, "
                "  embedding_profile_id = EXCLUDED.embedding_profile_id"
            ),
            [
                {
                    "id": row.id,
                    "ws": row.workspace_id,
                    "doc": row.document_id,
                    "heading": row.heading,
                    "content": row.content,
                    "tokens": row.token_count,
                    "ordinal": row.chunk_ordinal,
                    "hash": row.chunk_hash,
                    "vector": "[" + ",".join(f"{value:.8f}" for value in vector) + "]",
                    "profile": profile_id,
                    "line_start": row.line_start,
                    "line_end": row.line_end,
                }
                for row, vector in zip(rows, vectors, strict=True)
            ],
        )
        processed += len(rows)
        coverage = await _coverage(session, table=table)
        await session.execute(
            text(
                "UPDATE embedding_reembed_jobs SET processed_chunks = :done, "
                "  status = 'running' WHERE id = :id"
            ),
            {"done": processed, "id": job_id},
        )
        await session.execute(
            text("UPDATE embedding_profiles SET coverage = :c WHERE id = :id"),
            {"c": coverage, "id": profile_id},
        )
        await session.commit()
        # 同上：commit 后必须重新绑定，否则下一轮 SELECT 恒 0 行。
        await _bind(session, workspace_id, None)

    coverage = await _coverage(session, table=table)
    await session.execute(
        text(
            "UPDATE embedding_reembed_jobs SET status = 'succeeded', "
            "  processed_chunks = :done, finished_at = now() WHERE id = :id"
        ),
        {"done": processed, "id": job_id},
    )
    await session.execute(
        text("UPDATE embedding_profiles SET coverage = :c WHERE id = :id"),
        {"c": coverage, "id": profile_id},
    )
    await session.commit()
    return {"status": "succeeded", "processed": processed, "coverage": coverage}


async def _fail_job(
    session: AsyncSession, job_id: UUID, processed: int, reason: str
) -> None:
    await session.execute(
        text(
            "UPDATE embedding_reembed_jobs SET status = 'failed', "
            "  processed_chunks = :done, error = :reason, finished_at = now() "
            "WHERE id = :id"
        ),
        {"done": processed, "reason": reason, "id": job_id},
    )
    await session.commit()


async def activate_profile(
    session: AsyncSession, *, workspace_id: UUID, profile_id: UUID
) -> dict[str, Any]:
    """覆盖率 1.0 才允许原子切换（PHASE5-SPEC D9）。"""
    profile = (
        await session.execute(
            text(
                "SELECT id, dimension, chunk_table, status FROM embedding_profiles "
                "WHERE id = :id AND workspace_id = :ws FOR UPDATE"
            ),
            {"id": profile_id, "ws": workspace_id},
        )
    ).first()
    if profile is None:
        raise AppError(ErrorCode.NOT_FOUND)
    table = validate_chunk_table(str(profile.chunk_table))
    coverage = await _coverage(session, table=table)
    if coverage < 1.0:
        raise AppError(
            ErrorCode.CONFLICT,
            details={"reason": "COVERAGE_INCOMPLETE", "coverage": round(coverage, 4)},
        )

    previous = (
        await session.execute(
            text(
                "SELECT embedding_profile_id FROM workspace_settings "
                "WHERE workspace_id = :ws FOR UPDATE"
            ),
            {"ws": workspace_id},
        )
    ).scalar_one_or_none()

    # 先退役旧 profile 再激活新 profile：顺序反了会瞬时违反
    # `uq_embedding_active` 部分唯一索引。
    if previous is not None:
        await session.execute(
            text(
                "UPDATE embedding_profiles SET status = 'retired', "
                "  retire_at = now() + make_interval(days => :days) WHERE id = :id"
            ),
            {"days": EMBEDDING_RETIRE_DAYS, "id": previous},
        )
    await session.execute(
        text(
            "UPDATE embedding_profiles SET status = 'active', activated_at = now(), "
            "  previous_profile_id = :previous WHERE id = :id"
        ),
        {"previous": previous, "id": profile_id},
    )
    await session.execute(
        text(
            "UPDATE workspace_settings SET embedding_profile_id = :profile, "
            "  updated_at = now() WHERE workspace_id = :ws"
        ),
        {"profile": profile_id, "ws": workspace_id},
    )
    await session.commit()
    return {"activated": str(profile_id), "coverage": coverage}


async def rollback_profile(
    session: AsyncSession, *, workspace_id: UUID, user_id: UUID, role: str
) -> EmbeddingSettings:
    """把 active profile 指回 `previous_profile_id`（AC-50 回滚断言）。"""
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    active = await _active_profile_row(session, workspace_id=workspace_id)
    if active is None or active.get("previous_profile_id") is None:
        raise AppError(ErrorCode.CONFLICT, details={"reason": "NO_PREVIOUS_PROFILE"})
    previous_id = active["previous_profile_id"]

    await session.execute(
        text("UPDATE embedding_profiles SET status = 'retired' WHERE id = :id"),
        {"id": active["id"]},
    )
    await session.execute(
        text(
            "UPDATE embedding_profiles SET status = 'active', retire_at = NULL, "
            "  activated_at = now() WHERE id = :id"
        ),
        {"id": previous_id},
    )
    await session.execute(
        text(
            "UPDATE workspace_settings SET embedding_profile_id = :profile, "
            "  updated_at = now() WHERE workspace_id = :ws"
        ),
        {"profile": previous_id, "ws": workspace_id},
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="settings.embedding.rolled_back",
        target=str(previous_id),
        detail={"from": str(active["id"])},
    )
    await session.commit()
    await _bind(session, workspace_id, user_id)
    return await get_embedding_settings(session, workspace_id=workspace_id)


async def chunk_table(session: AsyncSession, *, workspace_id: UUID) -> str:
    """当前 workspace 的向量表名；无 active profile 时为基表。"""
    active = await _active_profile_row(session, workspace_id=workspace_id)
    if active is None or not active.get("chunk_table"):
        return BASE_CHUNK_TABLE
    return validate_chunk_table(str(active["chunk_table"]))


async def active_embedding_params(
    session: AsyncSession, *, workspace_id: UUID
) -> dict[str, Any]:
    """查询侧必须用 active profile 的模型与维度（PHASE5-SPEC 风险 5）。

    无 active profile 时回落环境变量配置。
    """
    active = await _active_profile_row(session, workspace_id=workspace_id)
    if active is None:
        return {
            "model": app_settings.embedding_model,
            "dimension": app_settings.embedding_dimension,
            "provider": app_settings.embedding_provider,
        }
    return {
        "model": str(active["model"]),
        "dimension": int(active["dimension"]),
        "provider": str(active["provider"]),
    }


async def start_reembed_in_background(
    workspace_id: UUID, profile_id: UUID, job_id: UUID
) -> asyncio.Task[Any]:
    """进程内后台任务（与 chat/review 一致；重启后由 dispatcher 补算）。"""
    from mylife_api.db import get_session_factory

    async def _run() -> None:
        factory = get_session_factory()
        async with factory() as session:
            await _bind(session, workspace_id, None)
            try:
                await run_reembed(
                    session,
                    workspace_id=workspace_id,
                    profile_id=profile_id,
                    job_id=job_id,
                )
            except Exception:  # noqa: BLE001 - 后台任务不得拖垮进程
                logger.exception("reembed failed workspace=%s", workspace_id)
                await session.rollback()

    task = asyncio.create_task(_run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


__all__ = [
    "activate_profile",
    "chunk_table",
    "create_profile",
    "get_embedding_settings",
    "rollback_profile",
    "run_reembed",
    "start_reembed_in_background",
    "test_embedding",
]
