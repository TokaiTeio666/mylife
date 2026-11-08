"""租约回收与复盘续跑（NFR-4；FX-09；PHASE4-SPEC §6.3）。

- 过期租约：`{active} → retry_wait`（reason=`lease_expired`，retry_from_state 记录原状态）；
- `created` / `retry_wait` 的复盘 run 由 API 进程内的 sweeper 拉起；
- 同 period 不会产生重复产物（唯一索引 + 版本指针 + 文件覆盖）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api.db import get_session_factory, set_rls_context

logger = logging.getLogger("mylife.api.lease")

_inflight: set[UUID] = set()


async def sweep_expired_leases(session: AsyncSession) -> int:
    """把过期活跃租约转 `retry_wait`；返回回收数量。"""
    result = await session.execute(
        text(
            "SELECT workspace_id, run_id, state, retry_from_state, lease_expires_at "
            "FROM dispatch_resumable_review_runs(50)"
        )
    )
    rows = [dict(row._mapping) for row in result]
    reclaimed = 0
    for row in rows:
        if row["state"] in {"created", "retry_wait"}:
            continue
        # SECURITY DEFINER 扫描返回跨租户行；真正更新前必须绑定租户上下文。
        await set_rls_context(session, row["workspace_id"], None)
        transitioned = await session.execute(
            text(
                "UPDATE agent_runs SET state = 'retry_wait', "
                "  retry_from_state = :state, lease_owner = NULL, "
                "  lease_expires_at = NULL, attempt_id = :attempt, updated_at = now(), "
                "  state_payload = jsonb_set(state_payload, '{attempts,lease_expired}', "
                "    to_jsonb(COALESCE("
                "      (state_payload->'attempts'->>'lease_expired')::int, 0) + 1)) "
                "WHERE workspace_id = :ws AND id = :run AND state = :state "
                "  AND (lease_expires_at IS NULL OR lease_expires_at < now()) "
                "RETURNING id"
            ),
            {
                "ws": row["workspace_id"],
                "run": row["run_id"],
                "state": row["state"],
                "attempt": uuid4(),
            },
        )
        if transitioned.first() is None:
            continue
        reclaimed += 1
        await session.execute(
            text(
                "INSERT INTO run_events "
                "(workspace_id, run_id, from_state, to_state, event, payload, stream_epoch) "
                "VALUES (:ws, :run, :from, 'retry_wait', 'run.state', CAST(:payload AS jsonb), 0)"
            ),
            {
                "ws": row["workspace_id"],
                "run": row["run_id"],
                "from": row["state"],
                "payload": json.dumps(
                    {
                        "event": "state.transition",
                        "from_state": row["state"],
                        "to_state": "retry_wait",
                        "reason": "lease_expired",
                        "retry_from_state": row["state"],
                    }
                ),
            },
        )
    return reclaimed


async def resume_pending_runs(*, limit: int = 20) -> int:
    """拉起 `created`/`retry_wait` 的复盘 run（进程内 asyncio）。"""

    factory = get_session_factory()
    async with factory() as session:
        # `has_review` 必须由 SECURITY DEFINER 函数返回：外部 JOIN agent_runs
        # 会重新受 RLS 约束（无租户上下文 → 0 行）。
        result = await session.execute(
            text(
                "SELECT workspace_id, run_id, state, has_review "
                "FROM dispatch_resumable_review_runs(:limit)"
            ),
            {"limit": limit},
        )
        rows = [dict(row._mapping) for row in result]
        await session.rollback()

    started = 0
    for row in rows:
        if row["state"] not in {"created", "retry_wait"}:
            continue
        # 只有带 review_id 的 run 才能被 runner 接续（历史 chat/异常行跳过）。
        if not row.get("has_review"):
            continue
        run_id = row["run_id"]
        if run_id in _inflight:
            continue
        _inflight.add(run_id)
        task = asyncio.create_task(_run_and_forget(run_id, row["workspace_id"]))
        started += 1
        _ = task
    return started


async def _run_and_forget(run_id: UUID, workspace_id: UUID) -> None:
    from mylife_api.review_runner import run_review

    try:
        await run_review(run_id, workspace_id)
    finally:
        _inflight.discard(run_id)


async def review_sweeper_tick() -> dict[str, int]:
    factory = get_session_factory()
    reclaimed = 0
    async with factory() as session:
        try:
            reclaimed = await sweep_expired_leases(session)
            await session.commit()
        except Exception:  # noqa: BLE001 - sweeper 不得拖垮 API
            await session.rollback()
            logger.exception("lease sweeper failed")
    resumed = await resume_pending_runs()
    if reclaimed or resumed:
        logger.info("lease_sweeper reclaimed=%s resumed=%s", reclaimed, resumed)
    return {"reclaimed": reclaimed, "resumed": resumed}


async def review_sweeper_loop(interval_seconds: int = 15) -> None:
    while True:
        try:
            await review_sweeper_tick()
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # noqa: BLE001
            logger.exception("review sweeper loop error")
        await asyncio.sleep(interval_seconds)


__all__ = [
    "resume_pending_runs",
    "review_sweeper_loop",
    "review_sweeper_tick",
    "sweep_expired_leases",
]
