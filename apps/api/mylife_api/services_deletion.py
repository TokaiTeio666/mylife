"""删除冷却与 purge（SEC-27；API-CONTRACTS §3.11.4–§3.11.7）。

Phase 4 只实现 SEC-27 所需子集：requested → cooling → purged / cancelled。
导出 ZIP 与完整撤销演练属 Phase 5（PHASE4-SPEC D17/D20）。
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from mylife_contract.constants import DELETION_NOTICE
from mylife_contract.deletions import DeletionNotice, DeletionRequest
from mylife_contract.errors import ErrorCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories
from mylife_api.db import set_rls_context
from mylife_api.errors import AppError
from mylife_api.ids import uuid7
from mylife_api.repositories import insert_audit
from mylife_api.settings import settings

logger = logging.getLogger("mylife.api.deletion")

#: purge 时保留的表（审计与平台记录）；正文表见 DELETION_PURGE_TABLES。
RETAINED_TABLES = {"audit_logs", "deletion_requests", "users", "workspaces", "invite_codes"}


def _notice() -> DeletionNotice:
    return DeletionNotice(**DELETION_NOTICE)  # type: ignore[arg-type]


def _model(row: dict[str, Any]) -> DeletionRequest:
    return DeletionRequest(
        deletion_id=row["id"],
        scope=row["scope"],
        state=row["state"],
        purge_at=row["purge_at"],
        # 冷却期内可撤销；截止时间与 purge_at 相同（PHASE5-SPEC D17）。
        cancellable_until=row["purge_at"],
        requested_at=row["created_at"],
        requested_by=row["requested_by"],
        can_cancel=row["state"] == "cooling",
        export_recommended=row["state"] == "cooling",
        notice=_notice(),
    )


async def create_deletion(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
    scope: str,
    confirm_text: str,
    reason: str | None,
    skip_export: bool = False,
) -> dict[str, Any]:
    # API §3.11.4 要求 owner；PHASE5-SPEC D15 保留 admin 代操作但需显式原因。
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    workspace = await repositories.get_workspace(session, workspace_id)
    if workspace is None:
        raise AppError(ErrorCode.NOT_FOUND)
    expected = workspace["name"] if scope == "workspace" else "DELETE MY ACCOUNT"
    if confirm_text.strip() != expected:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, details={"field": "confirm_text"}
        )
    active = await session.execute(
        text(
            "SELECT count(*) FROM agent_runs WHERE workspace_id = :ws "
            "AND is_active_state(state)"
        ),
        {"ws": workspace_id},
    )
    if int(active.scalar_one()) > 0:
        raise AppError(ErrorCode.CONFLICT, details={"reason": "ACTIVE_RUN"})
    existing = await session.execute(
        text(
            "SELECT state FROM deletion_requests WHERE workspace_id = :ws "
            "AND state IN ('requested','cooling') ORDER BY created_at DESC LIMIT 1"
        ),
        {"ws": workspace_id},
    )
    existing_row = existing.first()
    if existing_row is not None:
        # 冷却中重复申请 → DELETE_IN_COOLING（API §3.11.4）；
        # `requested` 态仍按 DELETE_ALREADY_REQUESTED 处理。
        if existing_row.state == "cooling":
            raise AppError(ErrorCode.DELETE_IN_COOLING)
        raise AppError(ErrorCode.DELETE_ALREADY_REQUESTED)

    # D15：默认要求 7 天内有成功导出；`skip_export` 由 owner 显式承担后果。
    if not skip_export:
        recent_export = (
            await session.execute(
                text(
                    "SELECT 1 FROM export_jobs WHERE workspace_id = :ws "
                    "AND status IN ('ready','succeeded') "
                    "AND created_at > now() - interval '7 days' LIMIT 1"
                ),
                {"ws": workspace_id},
            )
        ).first()
        if recent_export is None:
            raise AppError(
                ErrorCode.CONFLICT,
                details={
                    "reason": "EXPORT_REQUIRED",
                    "message": "请先导出一份数据备份，或使用 skip_export 明确跳过",
                },
            )
    elif role != "owner":
        # 跳过导出只有 owner 能决定。
        raise AppError(ErrorCode.FORBIDDEN, details={"reason": "OWNER_ONLY"})

    purge_at = datetime.now(UTC) + timedelta(days=settings.deletion_cooldown_days)
    deletion_id = uuid7()
    # D16：账户级删除对每个 owned workspace 各插一行（RLS 下逐个绑定）。
    targets = [workspace_id]
    if scope == "account":
        owned = (
            await session.execute(
                text("SELECT id FROM workspaces WHERE owner_id = :owner"),
                {"owner": user_id},
            )
        ).fetchall()
        targets = [row.id for row in owned] or [workspace_id]

    for target_ws in targets:
        await set_rls_context(session, target_ws, user_id)
        target_id = deletion_id if target_ws == workspace_id else uuid7()
        await session.execute(
            text(
                "INSERT INTO deletion_requests "
                "(id, workspace_id, scope, state, purge_at, requested_by) "
                "VALUES (:id, :ws, :scope, 'cooling', :purge_at, :user)"
            ),
            {
                "id": target_id,
                "ws": target_ws,
                "scope": scope,
                "purge_at": purge_at,
                "user": user_id,
            },
        )
    await set_rls_context(session, workspace_id, user_id)
    # 冷却期立即停用调度，避免继续生成产物；先记录哪些原本启用，
    # 撤销时按原状态恢复，不能把用户手动关闭的调度重新打开。
    enabled_rows = await session.execute(
        text(
            "SELECT kind FROM workspace_schedules "
            "WHERE workspace_id = :ws AND enabled"
        ),
        {"ws": workspace_id},
    )
    enabled_kinds = [row.kind for row in enabled_rows]
    await session.execute(
        text("UPDATE workspace_schedules SET enabled = false WHERE workspace_id = :ws"),
        {"ws": workspace_id},
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="deletion.requested",
        target=str(deletion_id),
        detail={
            "scope": scope,
            # `reason` 是用户自由文本；只保留截断后的摘要，避免正文进审计（§9.2）。
            "reason": (reason[:80] if reason else None),
            "purge_at": purge_at.isoformat(),
            "enabled_kinds": enabled_kinds,
            "skip_export": skip_export,
        },
    )
    await session.commit()
    await set_rls_context(session, workspace_id, user_id)
    row = await _get_row(session, workspace_id=workspace_id, deletion_id=deletion_id)
    assert row is not None
    return _model(row).model_dump(mode="json")


async def _get_row(
    session: AsyncSession, *, workspace_id: UUID, deletion_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, scope, state, purge_at, requested_by, cancelled_at, created_at "
            "FROM deletion_requests WHERE workspace_id = :ws AND id = :id"
        ),
        {"ws": workspace_id, "id": deletion_id},
    )
    row = result.first()
    return dict(row._mapping) if row else None


async def current_deletion(
    session: AsyncSession, *, workspace_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, scope, state, purge_at, requested_by, cancelled_at, created_at "
            "FROM deletion_requests WHERE workspace_id = :ws "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"ws": workspace_id},
    )
    row = result.first()
    return _model(dict(row._mapping)).model_dump(mode="json") if row else None


async def cancel_deletion(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
    deletion_id: UUID,
) -> dict[str, Any]:
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    row = await _get_row(session, workspace_id=workspace_id, deletion_id=deletion_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND)
    if row["state"] != "cooling":
        raise AppError(ErrorCode.DELETE_NOT_CANCELLABLE)
    await session.execute(
        text(
            "UPDATE deletion_requests SET state = 'cancelled', cancelled_at = now(), "
            "  updated_at = now() WHERE workspace_id = :ws AND id = :id"
        ),
        {"ws": workspace_id, "id": deletion_id},
    )
    # 只恢复“申请删除前本来就启用”的调度（记录在 deletion.requested 审计里）。
    prior = await session.execute(
        text(
            "SELECT detail->'enabled_kinds' AS kinds FROM audit_logs "
            "WHERE workspace_id = :ws AND action = 'deletion.requested' "
            "  AND target = :target ORDER BY created_at DESC LIMIT 1"
        ),
        {"ws": workspace_id, "target": str(deletion_id)},
    )
    kinds_row = prior.first()
    enabled_kinds = list(kinds_row.kinds or []) if kinds_row else []
    if enabled_kinds:
        await session.execute(
            text(
                "UPDATE workspace_schedules SET enabled = true "
                "WHERE workspace_id = :ws AND kind = ANY(:kinds)"
            ),
            {"ws": workspace_id, "kinds": enabled_kinds},
        )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="deletion.cancelled",
        target=str(deletion_id),
        detail={},
    )
    await session.commit()
    return {"state": "cancelled"}


async def purge_due_deletions(session: AsyncSession) -> int:
    """冷却到期：删除正文表与对象存储产物，保留审计与 Git 历史（SEC-27）。"""
    from mylife_contract.constants import DELETION_PURGE_TABLES

    # FORCE RLS：dispatcher 无租户上下文，必须走 SECURITY DEFINER 扫描函数。
    result = await session.execute(
        text("SELECT id, workspace_id, scope FROM dispatch_due_deletions(20)")
    )
    rows = [dict(row._mapping) for row in result]
    purged = 0
    # 无 workspace_id 列但含用户数据的表：按 owner 删除（DATA-MODEL §9.2 的 sessions）。
    owner_scoped = {"sessions"}
    for row in rows:
        workspace_id = row["workspace_id"]
        await set_rls_context(session, workspace_id, None)
        owner_id = (
            await session.execute(
                text("SELECT owner_id FROM workspaces WHERE id = :ws"),
                {"ws": workspace_id},
            )
        ).scalar_one_or_none()
        # 在删除 `embedding_profiles` 之前记录动态换维表名（document_chunks_v*），
        # 否则后续既无法删除这些表中的本租户行，也无法做 0 残留校验。
        from mylife_contract.chunk_tables import validate_chunk_table

        dynamic_rows = (
            await session.execute(
                text(
                    "SELECT DISTINCT chunk_table FROM embedding_profiles "
                    "WHERE workspace_id = :ws AND chunk_table <> 'document_chunks'"
                ),
                {"ws": workspace_id},
            )
        ).fetchall()
        dynamic_tables: list[str] = []
        for (raw_table,) in dynamic_rows:
            try:
                dynamic_tables.append(validate_chunk_table(str(raw_table)))
            except Exception:  # noqa: BLE001
                continue

        # 两轮：外键依赖解除后再删一次（顺序无关）。
        for _pass in range(2):
            for dyn_table in dynamic_tables:
                try:
                    async with session.begin_nested():
                        await session.execute(
                            text(f"DELETE FROM {dyn_table} WHERE workspace_id = :ws"),
                            {"ws": workspace_id},
                        )
                except Exception:  # noqa: BLE001
                    logger.warning("purge skip dynamic table=%s pass=%s", dyn_table, _pass)
            for table in DELETION_PURGE_TABLES:
                if table in RETAINED_TABLES:
                    continue
                if table in owner_scoped:
                    if owner_id is None:
                        continue
                    statement = text(
                        "DELETE FROM sessions WHERE user_id = :owner"
                    )
                    params = {"owner": owner_id}
                else:
                    statement = text(f"DELETE FROM {table} WHERE workspace_id = :ws")
                    params = {"ws": workspace_id}
                try:
                    # 每表一个 SAVEPOINT：外键顺序/缺表不会中止整个 purge 事务。
                    async with session.begin_nested():
                        await session.execute(statement, params)
                except Exception:  # noqa: BLE001 - 表可能不存在或受外键顺序影响
                    logger.warning("purge skip table=%s pass=%s", table, _pass)
        # AC-55：purge 后逐表校验 0 命中；残留不静默，记审计与结构化日志。
        residual = await _verify_purged(
            session,
            workspace_id=workspace_id,
            owner_id=owner_id,
            dynamic_tables=dynamic_tables,
        )
        if residual:
            logger.error(
                "deletion purge residual workspace=%s tables=%s",
                workspace_id,
                residual,
            )
            await insert_audit(
                session,
                workspace_id=None,
                user_id=None,
                action="deletion.purge_residual",
                target=str(row["id"]),
                detail={"workspace_id": str(workspace_id), "tables": residual[:20]},
            )
        await session.execute(
            text(
                "UPDATE deletion_requests SET state = 'purged', updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": row["id"]},
        )
        if row["scope"] == "account":
            await session.execute(
                text(
                    "UPDATE users SET status = 'deleted' WHERE id IN "
                    "(SELECT owner_id FROM workspaces WHERE id = :ws)"
                ),
                {"ws": workspace_id},
            )
        await insert_audit(
            session,
            workspace_id=None,
            user_id=None,
            action="deletion.purged",
            target=str(row["id"]),
            detail={"workspace_id": str(workspace_id), "scope": row["scope"]},
        )
        await session.commit()
        data_root = settings.data_root / "workspaces" / str(workspace_id)
        shutil.rmtree(data_root, ignore_errors=True)
        purged += 1
    return purged


async def _verify_purged(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    owner_id: UUID | None,
    dynamic_tables: list[str] | None = None,
) -> list[str]:
    """逐表确认 purge 后 0 行（AC-55）；`sessions` 按 owner 统计。"""
    from mylife_contract.chunk_tables import validate_chunk_table
    from mylife_contract.constants import DELETION_PURGE_TABLES

    residual: list[str] = []
    for table in DELETION_PURGE_TABLES:
        if table in RETAINED_TABLES:
            continue
        if table == "sessions":
            if owner_id is None:
                continue
            statement = text("SELECT count(*) FROM sessions WHERE user_id = :owner")
            params = {"owner": owner_id}
        else:
            statement = text(f"SELECT count(*) FROM {table} WHERE workspace_id = :ws")
            params = {"ws": workspace_id}
        try:
            async with session.begin_nested():
                count = (await session.execute(statement, params)).scalar_one()
            if int(count) > 0:
                residual.append(table)
        except Exception:  # noqa: BLE001 - 缺表不计入残留
            continue
    # 动态换维表（document_chunks_v*）不在常量列表里。
    tables = list(dynamic_tables or [])
    if not tables:
        profiles = (
            await session.execute(
                text(
                    "SELECT DISTINCT chunk_table FROM embedding_profiles "
                    "WHERE workspace_id = :ws AND chunk_table <> 'document_chunks'"
                ),
                {"ws": workspace_id},
            )
        ).fetchall()
        tables = [str(row[0]) for row in profiles]
    for table in tables:
        try:
            safe = validate_chunk_table(str(table))
            async with session.begin_nested():
                count = (
                    await session.execute(
                        text(f"SELECT count(*) FROM {safe} WHERE workspace_id = :ws"),
                        {"ws": workspace_id},
                    )
                ).scalar_one()
            if int(count) > 0:
                residual.append(safe)
        except Exception:  # noqa: BLE001 - 表可能已被退役删除
            continue
    return residual


__all__ = [
    "cancel_deletion",
    "create_deletion",
    "current_deletion",
    "purge_due_deletions",
]
