"""依赖注入：数据库、认证会话、RunContext 与角色校验。

关键不变量（ARCHITECTURE §2.5 / §1.3 / API-CONTRACTS §1.4）：
- `workspace_id` 只来自 `sessions.active_workspace_id`，并被服务端写入 RLS 设置；
- 任何跨租户访问统一 404，不返回 403（避免资源探测）；
- 认证失败返回 401，并区分过期/被撤销。
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import Depends, Request
from mylife_contract.constants import COOKIE_SESSION
from mylife_contract.errors import ErrorCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories as repo
from mylife_api.context import current_request_id
from mylife_api.db import get_session_factory, set_rls_context
from mylife_api.errors import AppError
from mylife_api.security import hash_session_token, utcnow


@dataclass(frozen=True, slots=True)
class AuthState:
    session_id: UUID
    user_id: UUID
    token_version: int
    active_workspace_id: UUID | None
    user: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunContext:
    """服务端唯一可信上下文；绝不从请求体/查询/路径读取 workspace_id。"""

    user_id: UUID
    workspace_id: UUID
    role: str
    session_id: UUID
    request_id: str
    is_platform_admin: bool = False
    #: 客户端 IP，供 AC-07 的审计字段使用。
    ip: str | None = None


async def get_db() -> AsyncIterator[AsyncSession]:
    """每请求一个事务。

    成功与已处理的业务错误（AppError 由错误处理器转为响应）都要提交，
    这样失败登录等审计记录才能落库；未处理异常回滚。
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except AppError:
            # 已处理的业务错误：保留同事务的审计写入（§1.13）。
            # 若事务已被数据库中止（如未包 savepoint 的约束冲突），回滚后抛出。
            with contextlib.suppress(Exception):
                await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


async def require_session(request: Request, db: AsyncSession = Depends(get_db)) -> AuthState:
    token = request.cookies.get(COOKIE_SESSION)
    if not token:
        raise AppError(ErrorCode.AUTH_SESSION_EXPIRED)
    row = await repo.get_session_by_token_hash(db, hash_session_token(token))
    if row is None:
        raise AppError(ErrorCode.AUTH_SESSION_EXPIRED)
    if row["revoked_at"] is not None:
        raise AppError(ErrorCode.AUTH_SESSION_REVOKED)
    expires_at: datetime = row["expires_at"]
    if expires_at <= utcnow():
        raise AppError(ErrorCode.AUTH_SESSION_EXPIRED)

    user = await repo.get_user_by_id(db, row["user_id"])
    if user is None:
        raise AppError(ErrorCode.AUTH_SESSION_REVOKED)
    if user["status"] == "disabled":
        raise AppError(ErrorCode.AUTH_ACCOUNT_DISABLED)
    if user["status"] == "deleted":
        raise AppError(ErrorCode.AUTH_SESSION_REVOKED)

    # 绑定用户上下文：workspaces/workspace_members 的成员级策略依赖 app.user_id。
    await set_rls_context(db, None, user["id"])
    return AuthState(
        session_id=row["id"],
        user_id=row["user_id"],
        token_version=row["token_version"],
        active_workspace_id=row["active_workspace_id"],
        user=user,
    )


#: 冷却期仍允许的路径前缀（删除撤销与认证本身）。
_COOLING_ALLOWED_PREFIXES = ("/api/v1/deletions", "/api/v1/auth", "/api/v1/admin")


async def _assert_workspace_writable(db: AsyncSession, *, workspace_id: UUID) -> None:
    """删除冷却期“立即停用所有写操作”（API §3.11.4 / PHASE5-SPEC D14）。

    只读请求与 `/deletions/*`、`/auth/*`、`/admin/*` 放行；其余写请求 403。
    """
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM deletion_requests WHERE workspace_id = :ws "
                "AND state = 'cooling' LIMIT 1"
            ),
            {"ws": workspace_id},
        )
    ).first()
    if row is not None:
        raise AppError(ErrorCode.DELETE_IN_COOLING)


async def get_run_context(
    request: Request,
    auth: AuthState = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> RunContext:
    if auth.active_workspace_id is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"reason": "NO_ACTIVE_WORKSPACE"})

    membership = await repo.get_membership(
        db, workspace_id=auth.active_workspace_id, user_id=auth.user_id
    )
    if membership is None:
        # 会话里指向了非成员 Workspace：对外统一 404（SEC-06），
        # 内部记 SECURITY_ABORT/TENANT_LEAK 审计且不可人工放行（SEC-25）。
        await repo.insert_audit(
            db,
            workspace_id=None,
            user_id=auth.user_id,
            action="security_abort",
            target=str(auth.active_workspace_id),
            detail={"reason": "TENANT_LEAK", "external_status": 404},
        )
        raise AppError(ErrorCode.NOT_FOUND)

    await set_rls_context(db, auth.active_workspace_id, auth.user_id)

    # 冷却期只读（PHASE5-SPEC D14）：除只读请求与放行前缀外一律 403。
    path = request.url.path if request is not None else ""
    if (
        request is not None
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and not path.startswith(_COOLING_ALLOWED_PREFIXES)
    ):
        await _assert_workspace_writable(db, workspace_id=auth.active_workspace_id)

    return RunContext(
        user_id=auth.user_id,
        workspace_id=auth.active_workspace_id,
        role=membership["role"],
        session_id=auth.session_id,
        request_id=current_request_id(),
        is_platform_admin=bool(auth.user.get("is_platform_admin", False)),
        ip=client_ip(request) if request is not None else None,
    )


def require_owner(ctx: RunContext = Depends(get_run_context)) -> RunContext:
    if ctx.role != "owner":
        raise AppError(ErrorCode.FORBIDDEN)
    return ctx


async def require_platform_admin(auth: AuthState = Depends(require_session)) -> AuthState:
    if not bool(auth.user.get("is_platform_admin", False)):
        raise AppError(ErrorCode.ADMIN_REQUIRED)
    return auth


def now_utc() -> datetime:
    return datetime.now(UTC)
