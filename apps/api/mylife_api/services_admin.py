"""管理后台（API-CONTRACTS §3.10；AC-54 / SEC-30）。

跨租户读取一律走 migration 0011 里的 `admin_*` SECURITY DEFINER 函数：
`workspaces`/`audit_logs`/`usage_events` 都是 FORCE RLS，普通 SELECT 恒 0 行。
这些函数只返回元数据列——**不存在任何返回笔记/复盘/对话正文的路径**（SEC-30）。

禁用用户会同时撤销其全部会话（`revoked_at` + `token_version+1`），并拒绝
禁用自己或最后一个 platform_admin（API §3.10）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from mylife_contract.admin import (
    AdminAuditLog,
    AdminSystemConfigItem,
    AdminUsageResponse,
    AdminUsageWorkspace,
    AdminUser,
    AdminWorkspace,
    AdminWorkspaceCounts,
)
from mylife_contract.errors import ErrorCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories
from mylife_api.errors import AppError
from mylife_api.repositories import insert_audit
from mylife_api.settings import settings as app_settings

logger = logging.getLogger("mylife.api.admin")

#: 环境变量锁定的系统配置键：`PUT` 返回 `ADMIN_SYSTEM_CONFIG_LOCKED`（D5）。
ENV_LOCKED_KEYS: dict[str, str] = {
    "max_zip_bytes": "MYLIFE_MAX_ZIP_BYTES",
    "deletion_cooldown_days": "MYLIFE_DELETION_COOLDOWN_DAYS",
    "export_ttl_days": "MYLIFE_EXPORT_TTL_DAYS",
    "export_daily_limit": "MYLIFE_EXPORT_DAILY_LIMIT",
    "embedding_coverage_threshold": "MYLIFE_EMBEDDING_COVERAGE_THRESHOLD",
    "embedding_retire_days": "MYLIFE_EMBEDDING_RETIRE_DAYS",
    "dispatcher_interval_seconds": "MYLIFE_DISPATCHER_INTERVAL_SECONDS",
    "review_sweeper_interval_seconds": "MYLIFE_REVIEW_SWEEPER_INTERVAL_SECONDS",
}

#: 可写配置键的默认值（无 DB 行、非 env 锁定时返回）。
DEFAULT_CONFIG: dict[str, Any] = {
    "max_zip_bytes": 200 * 1024 * 1024,
    "deletion_cooldown_days": 30,
    "export_ttl_days": 7,
    "export_daily_limit": 3,
    "embedding_coverage_threshold": 0.995,
    "embedding_retire_days": 7,
    "dispatcher_interval_seconds": 60,
    "review_sweeper_interval_seconds": 15,
}

_WRITABLE_KEYS = frozenset(DEFAULT_CONFIG)


def _user(row: dict[str, Any]) -> AdminUser:
    return AdminUser(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        status=row["status"],
        created_at=row["created_at"],
        workspace_count=int(row.get("workspace_count") or 0),
        last_login_at=row.get("last_login_at"),
    )


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------


async def list_users(
    session: AsyncSession,
    *,
    q: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[AdminUser], int]:
    rows = await session.execute(
        text("SELECT * FROM admin_list_users(:limit, :offset, :q, :status)"),
        {"limit": limit, "offset": offset, "q": q, "status": status},
    )
    total = (
        await session.execute(
            text("SELECT admin_count_users(:q, :status)"), {"q": q, "status": status}
        )
    ).scalar_one()
    return [_user(dict(row._mapping)) for row in rows], int(total)


async def get_user(session: AsyncSession, *, user_id: UUID) -> AdminUser:
    # `users` 不是租户表（无 RLS），可以直接按主键读；计数仍需跨租户聚合。
    row = (
        await session.execute(
            text(
                "SELECT u.id, u.email, u.display_name, u.status, u.created_at, "
                "       u.last_login_at, "
                "       (SELECT count(*) FROM workspace_members wm "
                "        WHERE wm.user_id = u.id) AS workspace_count "
                "FROM users u WHERE u.id = :id"
            ),
            {"id": user_id},
        )
    ).first()
    if row is None:
        raise AppError(ErrorCode.ADMIN_USER_NOT_FOUND)
    return _user(dict(row._mapping))


async def disable_user(
    session: AsyncSession,
    *,
    admin_id: UUID,
    user_id: UUID,
    reason: str,
    ip: str | None = None,
) -> AdminUser:
    if admin_id == user_id:
        raise AppError(ErrorCode.CONFLICT, details={"reason": "CANNOT_DISABLE_SELF"})

    target = await repositories.get_user_by_id(session, user_id)
    if target is None:
        raise AppError(ErrorCode.ADMIN_USER_NOT_FOUND)
    if target["status"] == "disabled":
        raise AppError(ErrorCode.ADMIN_USER_DISABLED)

    if target.get("is_platform_admin"):
        remaining = (
            await session.execute(
                text(
                    "SELECT count(*) FROM users WHERE is_platform_admin "
                    "AND status = 'active' AND id <> :id"
                ),
                {"id": user_id},
            )
        ).scalar_one()
        if int(remaining) == 0:
            raise AppError(ErrorCode.CONFLICT, details={"reason": "LAST_PLATFORM_ADMIN"})

    await session.execute(
        text(
            "UPDATE users SET status = 'disabled', disabled_reason = :reason, "
            "  disabled_at = now(), updated_at = now() WHERE id = :id"
        ),
        {"reason": reason, "id": user_id},
    )
    # 已有会话立即失效（AC-54）。
    await repositories.revoke_all_sessions(session, user_id)
    await insert_audit(
        session,
        workspace_id=None,
        user_id=admin_id,
        action="admin.user.disabled",
        target=str(user_id),
        detail={"reason": reason},
        ip=ip,
    )
    return await _get_user_after_update(session, user_id)


async def enable_user(
    session: AsyncSession, *, admin_id: UUID, user_id: UUID, ip: str | None = None
) -> AdminUser:
    target = await repositories.get_user_by_id(session, user_id)
    if target is None:
        raise AppError(ErrorCode.ADMIN_USER_NOT_FOUND)
    await session.execute(
        text(
            "UPDATE users SET status = 'active', disabled_reason = NULL, "
            "  disabled_at = NULL, updated_at = now() WHERE id = :id AND status = 'disabled'"
        ),
        {"id": user_id},
    )
    await insert_audit(
        session,
        workspace_id=None,
        user_id=admin_id,
        action="admin.user.enabled",
        target=str(user_id),
        detail={},
        ip=ip,
    )
    return await _get_user_after_update(session, user_id)


async def _get_user_after_update(session: AsyncSession, user_id: UUID) -> AdminUser:
    # 新开 savepoint 读取，避免事务内旧快照。
    async with session.begin_nested():
        return await get_user(session, user_id=user_id)


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


def _workspace(row: dict[str, Any]) -> AdminWorkspace:
    return AdminWorkspace(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        owner_id=row["owner_id"],
        plan=row["plan"],
        created_at=row["created_at"],
        counts=AdminWorkspaceCounts(
            documents=int(row.get("documents") or 0),
            reviews=int(row.get("reviews") or 0),
            tokens_month=int(row.get("tokens_month") or 0),
        ),
        deletion_state=row.get("deletion_state"),
    )


async def list_workspaces(
    session: AsyncSession, *, q: str | None, limit: int, offset: int
) -> tuple[list[AdminWorkspace], int]:
    rows = await session.execute(
        text("SELECT * FROM admin_list_workspaces(:limit, :offset, :q)"),
        {"limit": limit, "offset": offset, "q": q},
    )
    total = (
        await session.execute(text("SELECT admin_count_workspaces(:q)"), {"q": q})
    ).scalar_one()
    return [_workspace(dict(row._mapping)) for row in rows], int(total)


async def get_workspace(session: AsyncSession, *, workspace_id: UUID) -> AdminWorkspace:
    row = (
        await session.execute(
            text("SELECT * FROM admin_get_workspace(:id)"), {"id": workspace_id}
        )
    ).first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return _workspace(dict(row._mapping))


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------

#: 审计 detail 中绝不回传的键（SEC-30 / SEC-19）。
_SENSITIVE_KEYS = {"api_key", "key", "password", "token", "secret", "credential"}


def _safe_detail(detail: Any) -> dict[str, Any]:
    data = detail if isinstance(detail, dict) else {}
    return {
        key: value
        for key, value in data.items()
        if key.lower() not in _SENSITIVE_KEYS
    }


async def list_audit_logs(
    session: AsyncSession,
    *,
    action: str | None,
    workspace_id: UUID | None,
    from_ts: datetime | None,
    to_ts: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[AdminAuditLog], int]:
    rows = await session.execute(
        text(
            "SELECT * FROM admin_list_audit_logs("
            ":limit, :offset, :action, :ws, :from_ts, :to_ts)"
        ),
        {
            "limit": limit,
            "offset": offset,
            "action": action,
            "ws": workspace_id,
            "from_ts": from_ts,
            "to_ts": to_ts,
        },
    )
    total = (
        await session.execute(
            text(
                "SELECT admin_count_audit_logs(:action, :ws, :from_ts, :to_ts)"
            ),
            {"action": action, "ws": workspace_id, "from_ts": from_ts, "to_ts": to_ts},
        )
    ).scalar_one()
    items = [
        AdminAuditLog(
            id=row.id,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            action=row.action,
            target=row.target,
            detail=_safe_detail(row.detail),
            ip=str(row.ip) if row.ip is not None else None,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return items, int(total)


# ---------------------------------------------------------------------------
# 系统配置（D5）
# ---------------------------------------------------------------------------


async def list_system_config(session: AsyncSession) -> list[AdminSystemConfigItem]:
    rows = {
        row.key: row.value_json
        for row in await session.execute(
            text("SELECT key, value_json FROM platform_settings")
        )
    }
    items: list[AdminSystemConfigItem] = []
    for key in sorted(DEFAULT_CONFIG):
        value = rows.get(key, DEFAULT_CONFIG[key])
        locked = key in ENV_LOCKED_KEYS
        if locked:
            value = getattr(app_settings, key, DEFAULT_CONFIG[key])
        items.append(
            AdminSystemConfigItem(
                key=key,
                value=value,
                locked=locked,
                source="env" if locked else ("db" if key in rows else "default"),
            )
        )
    return items


async def update_system_config(
    session: AsyncSession,
    *,
    admin_id: UUID,
    key: str,
    value: Any,
    ip: str | None = None,
) -> AdminSystemConfigItem:
    if key not in _WRITABLE_KEYS:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, details={"field": "key", "reason": "UNKNOWN_KEY"}
        )
    if key in ENV_LOCKED_KEYS:
        raise AppError(
            ErrorCode.ADMIN_SYSTEM_CONFIG_LOCKED,
            details={"key": key, "env": ENV_LOCKED_KEYS[key]},
        )
    await session.execute(
        text(
            "INSERT INTO platform_settings (key, value_json, updated_by) "
            "VALUES (:key, CAST(:value AS jsonb), :admin) "
            "ON CONFLICT (key) DO UPDATE SET value_json = EXCLUDED.value_json, "
            "  updated_at = now(), updated_by = EXCLUDED.updated_by"
        ),
        {"key": key, "value": _json_literal(value), "admin": admin_id},
    )
    await insert_audit(
        session,
        workspace_id=None,
        user_id=admin_id,
        action="admin.system_config.updated",
        target=key,
        detail={"key": key},
        ip=ip,
    )
    return AdminSystemConfigItem(key=key, value=value, locked=False, source="db")


def _json_literal(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 用量（跨 workspace 聚合）
# ---------------------------------------------------------------------------


async def usage(
    session: AsyncSession, *, from_ts: datetime, to_ts: datetime
) -> AdminUsageResponse:
    rows = await session.execute(
        text("SELECT * FROM admin_usage_totals(:from_ts, :to_ts)"),
        {"from_ts": from_ts, "to_ts": to_ts},
    )
    items: list[AdminUsageWorkspace] = []
    total_prompt = total_completion = total_calls = 0
    total_cost = 0.0
    for row in rows:
        items.append(
            AdminUsageWorkspace(
                workspace_id=row.workspace_id,
                prompt_tokens=int(row.prompt_tokens),
                completion_tokens=int(row.completion_tokens),
                calls=int(row.calls),
                cost=round(float(row.cost), 4),
            )
        )
        total_prompt += int(row.prompt_tokens)
        total_completion += int(row.completion_tokens)
        total_calls += int(row.calls)
        total_cost += float(row.cost)
    return AdminUsageResponse(
        totals=AdminUsageWorkspace(
            workspace_id=UUID(int=0),
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            calls=total_calls,
            cost=round(total_cost, 4),
        ),
        items=items,
    )


# ---------------------------------------------------------------------------
# 邀请码撤销
# ---------------------------------------------------------------------------


async def revoke_invite_code(
    session: AsyncSession, *, admin_id: UUID, code: str, ip: str | None = None
) -> None:
    row = (
        await session.execute(
            text("SELECT id, used_at FROM invite_codes WHERE code = :code"),
            {"code": code},
        )
    ).first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND)
    if row.used_at is not None:
        raise AppError(ErrorCode.CONFLICT, details={"reason": "INVITE_ALREADY_USED"})
    await session.execute(
        text("DELETE FROM invite_codes WHERE code = :code"), {"code": code}
    )
    await insert_audit(
        session,
        workspace_id=None,
        user_id=admin_id,
        action="admin.invite_code.revoked",
        target=code,
        detail={},
        ip=ip,
    )


# ---------------------------------------------------------------------------
# 限流（D27）
# ---------------------------------------------------------------------------

ADMIN_RATE_LIMIT = 100
ADMIN_RATE_WINDOW = 60


async def enforce_admin_rate_limit(user_id: UUID) -> None:
    from mylife_api.ratelimit import get_limiter

    result = await get_limiter().hit(
        f"rl:admin:{user_id}", ADMIN_RATE_LIMIT, ADMIN_RATE_WINDOW
    )
    if not result.allowed:
        retry_after = max(1, result.reset_at - int(datetime.now(UTC).timestamp()))
        raise AppError(
            ErrorCode.RATE_LIMITED,
            headers={"Retry-After": str(retry_after)},
        )


__all__ = [
    "disable_user",
    "enable_user",
    "enforce_admin_rate_limit",
    "get_user",
    "get_workspace",
    "list_audit_logs",
    "list_system_config",
    "list_users",
    "list_workspaces",
    "revoke_invite_code",
    "update_system_config",
    "usage",
]
