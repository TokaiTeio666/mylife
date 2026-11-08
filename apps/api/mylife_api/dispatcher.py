"""DB 驱动调度器（ARCHITECTURE §11/§16.6；AC-43/AC-52/FX-09）。

单副本运行：`python -m mylife_api.dispatcher`。每 tick：
1. 心跳；
2. 到期复盘调度（含 catchup 窗口与 period_already_succeeded）；
3. 到期 Git 源连接定时拉取；
4. 过期租约回收；
5. 删除冷却到期 purge；
6. outbox 重投。
跨租户到期扫描走 `dispatch_*` SECURITY DEFINER 函数（PHASE4-SPEC D26）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repo_review
from mylife_api.db import get_session_factory, set_rls_context
from mylife_api.schedule import due_period, missed_occurrences, next_run_at
from mylife_api.settings import settings

logger = logging.getLogger("mylife.dispatcher")

LOCK_NAME = "mylife-dispatch"


def _log(event: str, **fields: Any) -> None:
    # 结构化字段放进 extra，由 JsonFormatter 平铺为独立键（NFR-5）。
    safe = {
        key: value
        for key, value in fields.items()
        if key not in {"content", "quote", "message", "token", "secret", "password"}
    }
    logger.info("", extra={"event": event, **safe})


async def _set_context(session: AsyncSession, workspace_id: Any) -> None:
    await set_rls_context(session, workspace_id, None)


async def _heartbeat(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO dispatcher_heartbeats (id, last_tick_at, version) "
            "VALUES ('default', now(), '1') "
            "ON CONFLICT (id) DO UPDATE SET last_tick_at = now(), version = '1'"
        )
    )


async def dispatch_schedules(session: AsyncSession) -> list[dict[str, Any]]:
    from mylife_api import services_review

    result = await session.execute(
        text("SELECT * FROM dispatch_due_schedules(50)")
    )
    rows = [dict(row._mapping) for row in result]
    _log("dispatch.due_scan", count=len(rows))
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        workspace_id = row["workspace_id"]
        kind = row["kind"]
        tz = row["timezone"]
        now = datetime.now(UTC)
        due_at = row["next_run_at"]
        # `next_run_at` 本身是“应运行时刻”，必须计入本次扫描（见 missed_occurrences）。
        since = due_at or now
        try:
            occurrences, exceeded = missed_occurrences(
                kind=kind,
                at=row["local_time"],
                tz=tz,
                since=since,
                now=now,
                catchup_days=int(row["catchup_days"]),
                due_at=due_at,
            )
        except Exception:  # noqa: BLE001 - 单行失败不拖垮 tick
            logger.exception("schedule compute failed ws=%s kind=%s", workspace_id, kind)
            continue

        await _set_context(session, workspace_id)
        row_outcomes: list[dict[str, Any]] = []
        skip_reason = "catchup_window_exceeded" if exceeded else None
        if exceeded:
            _log(
                "dispatch.catchup_exceeded",
                workspace_id=str(workspace_id),
                kind=kind,
            )
        for occurrence in occurrences:
            local_date = occurrence.astimezone(ZoneInfo(tz)).date()
            period = due_period(kind, local_date)
            if await repo_review.period_succeeded(
                session, workspace_id=workspace_id, kind=kind, period_key=period
            ):
                skip_reason = skip_reason or "period_already_succeeded"
                _log(
                    "dispatch.skip",
                    workspace_id=str(workspace_id),
                    kind=kind,
                    period_key=period,
                    reason="period_already_succeeded",
                )
                continue
            try:
                admitted = await services_review.admit_scheduled_review(
                    session,
                    workspace_id=workspace_id,
                    kind=kind,
                    period_key=period,
                    inline=False,
                )
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                await _set_context(session, workspace_id)
                await _enqueue_outbox(
                    session,
                    workspace_id=workspace_id,
                    topic="review.scheduled",
                    payload={"kind": kind, "period_key": period},
                    idempotency_key=f"schedule:{kind}:{period}",
                )
                admitted = {"admitted": False, "reason": type(exc).__name__}
            outcomes.append(admitted)
            row_outcomes.append(admitted)
            _log(
                "dispatch.admit",
                workspace_id=str(workspace_id),
                kind=kind,
                period_key=period,
                admitted=bool(admitted.get("admitted")),
                reason=admitted.get("reason"),
                run_id=str(admitted.get("run_id")) if admitted.get("run_id") else None,
            )
        # 并发/暂时失败不推进 next_run_at，留待下一 tick 重试；
        # 已成功或“同 period 已有活跃 run/已成功”才推进。
        # `active`（同 period 已有活跃 run）与 `concurrency` 都是暂时状态，
        # 下一 tick 仍需处理该到期点，因此不推进 next_run_at。
        retry_later = any(
            not item.get("admitted")
            and item.get("reason") in {"active", "concurrency", "exception"}
            for item in row_outcomes
        )
        if retry_later:
            # 保留 next_run_at，只记录本次跳过原因；下一 tick 继续处理。
            await session.execute(
                text(
                    "UPDATE workspace_schedules SET last_skip_reason = :reason "
                    "WHERE workspace_id = :ws AND kind = :kind"
                ),
                {"reason": skip_reason, "ws": workspace_id, "kind": kind},
            )
        else:
            next_at = next_run_at(kind, row["local_time"], tz, now)
            await session.execute(
                text(
                    "UPDATE workspace_schedules SET next_run_at = :next_at, "
                    "  last_skip_reason = :reason "
                    "WHERE workspace_id = :ws AND kind = :kind"
                ),
                {
                    "next_at": next_at,
                    "reason": skip_reason,
                    "ws": workspace_id,
                    "kind": kind,
                },
            )
        await session.commit()
    return outcomes


async def dispatch_git_syncs(session: AsyncSession) -> int:
    from mylife_api import services_ingest

    result = await session.execute(text("SELECT * FROM dispatch_due_git_syncs(50)"))
    rows = [dict(row._mapping) for row in result]
    dispatched = 0
    for row in rows:
        await _set_context(session, row["workspace_id"])
        try:
            outcome = await services_ingest.create_scheduled_sync(
                session,
                workspace_id=row["workspace_id"],
                connection_id=row["conn_id"],
                interval_minutes=int(row["sync_interval_minutes"]),
            )
            dispatched += 1 if outcome.get("status") == "queued" else 0
            _log(
                "dispatch.git_sync",
                workspace_id=str(row["workspace_id"]),
                conn_id=str(row["conn_id"]),
                status=outcome.get("status"),
            )
        except Exception:  # noqa: BLE001
            await session.rollback()
            logger.exception("scheduled git sync failed")
    return dispatched


async def _enqueue_outbox(
    session: AsyncSession,
    *,
    workspace_id: Any,
    topic: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO outbox_events "
            "(id, workspace_id, topic, payload_json, idempotency_key) "
            "VALUES (gen_random_uuid(), :ws, :topic, CAST(:payload AS jsonb), :key) "
            "ON CONFLICT (idempotency_key) DO NOTHING"
        ),
        {
            "ws": workspace_id,
            "topic": topic,
            "payload": json.dumps(payload, ensure_ascii=False),
            "key": idempotency_key,
        },
    )
    await session.commit()


async def relay_outbox(session: AsyncSession) -> int:
    from mylife_api import services_review

    # FORCE RLS：无租户上下文时必须走 SECURITY DEFINER 扫描函数。
    result = await session.execute(text("SELECT * FROM dispatch_due_outbox(20)"))
    rows = [dict(row._mapping) for row in result]
    sent = 0
    for row in rows:
        await _set_context(session, row["workspace_id"])
        payload = row["payload_json"] or {}
        try:
            if row["topic"] == "review.scheduled":
                await services_review.admit_scheduled_review(
                    session,
                    workspace_id=row["workspace_id"],
                    kind=payload.get("kind"),
                    period_key=payload.get("period_key"),
                    inline=False,
                )
            await session.execute(
                text(
                    "UPDATE outbox_events SET status = 'sent', sent_at = now() WHERE id = :id"
                ),
                {"id": row["id"]},
            )
            await session.commit()
            sent += 1
        except Exception:  # noqa: BLE001
            await session.rollback()
            attempts = int(row["attempts"]) + 1
            await session.execute(
                text(
                    "UPDATE outbox_events SET attempts = :attempts, "
                    "  status = CASE WHEN :attempts >= 5 THEN 'dead' ELSE 'failed' END, "
                    "  available_at = now() + (:attempts * interval '30 seconds') "
                    "WHERE id = :id"
                ),
                {"attempts": attempts, "id": row["id"]},
            )
            await session.commit()
    return sent


async def _purge_deletions(session: AsyncSession) -> int:
    try:
        from mylife_api import services_deletion

        return await services_deletion.purge_due_deletions(session)
    except ImportError:  # pragma: no cover - C9 之前
        return 0


async def _expire_exports(session: AsyncSession) -> int:
    from mylife_api import services_export

    return await services_export.purge_expired_exports(session)


async def _fail_stale_ingest_jobs(session: AsyncSession) -> int:
    """AC-59：worker 被 kill -9 时 job_runs 会停在 running；30 分钟后置 failed。"""
    rows = await session.execute(text("SELECT * FROM dispatch_stale_ingest_jobs(50)"))
    count = 0
    for row in rows:
        result = await session.execute(
            text(
                "UPDATE job_runs SET status = 'failed', "
                "  error = 'INGEST_STALE_RUNNING', finished_at = now() "
                "WHERE id = :id AND status = 'running'"
            ),
            {"id": row.id},
        )
        if result.rowcount:
            count += 1
    await session.commit()
    return count


async def tick(session: AsyncSession) -> dict[str, Any]:
    """执行一轮调度；调用方保证 advisory lock 已持有。"""
    from mylife_api import services_lease

    await _heartbeat(session)
    await session.commit()
    outcomes = await dispatch_schedules(session)
    git_syncs = await dispatch_git_syncs(session)
    reclaimed = await services_lease.sweep_expired_leases(session)
    purged = await _purge_deletions(session)
    relayed = await relay_outbox(session)
    expired_exports = await _expire_exports(session)
    stale_jobs = await _fail_stale_ingest_jobs(session)
    await session.commit()
    return {
        "scheduled": len(outcomes),
        "admitted": sum(1 for item in outcomes if item.get("admitted")),
        "git_syncs": git_syncs,
        "reclaimed": reclaimed,
        "purged": purged,
        "outbox": relayed,
        "expired_exports": expired_exports,
        "stale_jobs": stale_jobs,
    }


async def run_once() -> dict[str, Any]:
    """获取会话级 advisory lock 后执行一轮；锁持有在独立连接上。

    `pg_try_advisory_lock` 是会话级的：若与业务事务共用连接，rollback 或
    连接归还池后锁可能残留。这里用专用连接持锁，tick 在独立事务中执行。
    """
    from mylife_api.db import get_engine

    engine = get_engine()
    factory = get_session_factory()
    async with engine.connect() as lock_conn:
        locked = await lock_conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": LOCK_NAME}
        )
        if not locked.scalar_one():
            _log("dispatch.lock_busy")
            return {"locked": False}
        try:
            async with factory() as session:
                return await tick(session)
        finally:
            await lock_conn.execute(
                text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": LOCK_NAME}
            )
            await lock_conn.commit()


async def run_forever() -> None:
    _log("dispatch.startup", interval=settings.dispatcher_interval_seconds)
    while True:
        try:
            result = await run_once()
            _log("dispatch.tick", **result)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # noqa: BLE001
            logger.exception("dispatcher tick failed")
        await asyncio.sleep(settings.dispatcher_interval_seconds)


def main() -> None:
    from mylife_api.logging_setup import configure_logging

    configure_logging(settings.log_level, "mylife-dispatcher")
    asyncio.run(run_forever())


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = [
    "dispatch_git_syncs",
    "dispatch_schedules",
    "relay_outbox",
    "run_forever",
    "run_once",
    "tick",
]
