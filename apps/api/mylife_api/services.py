"""业务服务：注册、登录、Workspace、邀请码。

所有错误以 `AppError` 表达，由统一处理器转成错误信封。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID

from mylife_contract.auth import (
    Limits,
    LoginResponse,
    MeResponse,
    RegisterResponse,
    SessionInfo,
    TokenBudget,
    User,
    Workspace,
)
from mylife_contract.constants import (
    CAPABILITIES_BY_ROLE,
    DEFAULT_LIMITS,
    INVITE_CODE_MAX_LENGTH,
    INVITE_CODE_MIN_LENGTH,
    LOGIN_FAILURE_WINDOW_SECONDS,
    LOGIN_MAX_FAILURES_PER_EMAIL_IP,
    LOGIN_MAX_PER_IP_PER_HOUR,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    REGISTER_MAX_PER_IP_PER_HOUR,
)
from mylife_contract.errors import ErrorCode
from mylife_contract.workspace import (
    SettingsSummary,
    WorkspaceCounts,
    WorkspaceDetail,
    WorkspaceListResponse,
    WorkspaceSwitchResponse,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories as repo
from mylife_api.db import set_rls_context
from mylife_api.errors import AppError
from mylife_api.ids import invite_code as make_invite_code
from mylife_api.ids import short_slug_suffix, uuid7
from mylife_api.ratelimit import get_limiter
from mylife_api.security import (
    hash_password,
    hash_session_token,
    new_csrf_token,
    new_session_token,
    session_expiry,
    utcnow,
    verify_password,
)

logger = logging.getLogger("mylife.api.services")

PERSONAL_WORKSPACE_NAME = "我的知识库"
DEFAULT_TIMEZONE = "Asia/Shanghai"


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def _user_model(row: dict[str, Any]) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        status=row["status"],
        created_at=row["created_at"],
        is_platform_admin=bool(row.get("is_platform_admin", False)),
    )


def _workspace_model(row: dict[str, Any], role: str) -> Workspace:
    return Workspace(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        role=role,
        plan=row["plan"],
        created_at=row["created_at"],
    )


def _limits() -> Limits:
    def budget(key: str) -> TokenBudget:
        raw = DEFAULT_LIMITS[key]  # type: ignore[index]
        return TokenBudget(**raw)  # type: ignore[arg-type]

    return Limits(
        review_concurrency=int(DEFAULT_LIMITS["review_concurrency"]),  # type: ignore[arg-type]
        daily_budget=budget("daily_budget"),
        monthly_budget=budget("monthly_budget"),
        max_zip_bytes=int(DEFAULT_LIMITS["max_zip_bytes"]),  # type: ignore[arg-type]
        max_search_top_k=int(DEFAULT_LIMITS["max_search_top_k"]),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


def _new_slug() -> str:
    return f"ws-{short_slug_suffix()}"


async def _create_personal_workspace(
    db: AsyncSession, *, user_id: UUID, name: str = PERSONAL_WORKSPACE_NAME
) -> dict[str, Any]:
    """创建 Workspace + owner 成员 + 默认设置 + 默认调度（FR-A3 / FR-E13）。"""
    workspace_id = uuid7()
    slug = _new_slug()
    for _ in range(3):
        if not await repo.slug_exists(db, slug):
            break
        slug = _new_slug()

    # RLS：先声明新 workspace 上下文，再插入（WITH CHECK id = app_workspace_id()）。
    await set_rls_context(db, workspace_id, user_id)
    row = await repo.insert_workspace(
        db, workspace_id=workspace_id, name=name, slug=slug, owner_id=user_id
    )
    await repo.insert_member(db, workspace_id=workspace_id, user_id=user_id, role="owner")
    await repo.insert_default_settings(db, workspace_id)
    await repo.insert_default_schedules(db, workspace_id)
    return row


async def _create_session(
    db: AsyncSession,
    *,
    user_id: UUID,
    active_workspace_id: UUID,
) -> tuple[str, str, datetime]:
    token = new_session_token()
    expires_at = session_expiry()
    await repo.insert_session(
        db,
        session_id=uuid7(),
        user_id=user_id,
        token_hash=hash_session_token(token),
        active_workspace_id=active_workspace_id,
        expires_at=expires_at,
    )
    return token, new_csrf_token(), expires_at


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------


async def register(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
    invite_code: str | None,
    ip: str | None,
) -> tuple[RegisterResponse, str, str]:
    """返回 (响应, session_token, csrf_token)。"""
    limiter = get_limiter()
    if ip:
        limited = await limiter.hit(
            f"rl:register:ip:{ip}", REGISTER_MAX_PER_IP_PER_HOUR, 3600
        )
        if not limited.allowed:
            raise AppError(
                ErrorCode.AUTH_RATE_LIMITED,
                headers={"Retry-After": str(max(1, limited.reset_at - int(_now_ts())))},
            )

    if invite_code is None or invite_code.strip() == "":
        raise AppError(ErrorCode.AUTH_INVITE_REQUIRED)
    invite_code = invite_code.strip()
    if not INVITE_CODE_MIN_LENGTH <= len(invite_code) <= INVITE_CODE_MAX_LENGTH:
        raise AppError(ErrorCode.AUTH_INVITE_INVALID)

    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise AppError(ErrorCode.AUTH_WEAK_PASSWORD)

    # 先锁定邀请码行，保证同码并发注册只有一个成功（AC-02 / SEC-05）。
    invite = await repo.get_invite_for_update(db, invite_code)
    if invite is None:
        raise AppError(ErrorCode.AUTH_INVITE_INVALID, details={"invite_code": invite_code})
    if invite["used_at"] is not None:
        raise AppError(ErrorCode.AUTH_INVITE_USED, details={"invite_code": invite_code})
    if invite["expires_at"] is not None and invite["expires_at"] <= utcnow():
        raise AppError(ErrorCode.AUTH_INVITE_EXPIRED, details={"invite_code": invite_code})

    if await repo.get_user_by_email(db, email) is not None:
        raise AppError(ErrorCode.AUTH_EMAIL_EXISTS)

    user_id = uuid7()
    try:
        # SAVEPOINT：并发同邮箱命中唯一约束时只回滚该语句，事务仍可提交审计。
        async with db.begin_nested():
            user_row = await repo.insert_user(
                db,
                user_id=user_id,
                email=email,
                password_hash=hash_password(password),
                display_name=display_name,
            )
    except IntegrityError as exc:  # 并发注册同邮箱
        raise AppError(ErrorCode.AUTH_EMAIL_EXISTS) from exc

    workspace_row = await _create_personal_workspace(db, user_id=user_id)

    await repo.mark_invite_used(db, code_id=invite["id"], user_id=user_id)

    token, csrf, expires_at = await _create_session(
        db, user_id=user_id, active_workspace_id=workspace_row["id"]
    )

    await repo.insert_audit(
        db,
        workspace_id=workspace_row["id"],
        user_id=user_id,
        action="user.registered",
        target=str(user_id),
        detail={"invite_code": invite_code, "workspace_id": str(workspace_row["id"])},
        ip=ip,
    )

    response = RegisterResponse(
        user=_user_model(user_row),
        workspace=_workspace_model(workspace_row, "owner"),
        session=SessionInfo(
            expires_at=expires_at,
            active_workspace_id=workspace_row["id"],
            csrf_token=csrf,
        ),
    )
    return response, token, csrf


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------


def _now_ts() -> int:
    return int(utcnow().timestamp())


def _locked_error(reset_at: int) -> AppError:
    retry_after = max(1, reset_at - _now_ts())
    return AppError(
        ErrorCode.AUTH_ACCOUNT_LOCKED,
        details={
            "locked_until": (
                datetime.now(UTC) + timedelta(seconds=retry_after)
            ).isoformat().replace("+00:00", "Z")
        },
        headers={"Retry-After": str(retry_after)},
    )


async def login(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    ip: str | None,
    current_session_id: UUID | None = None,
) -> tuple[LoginResponse, str, str]:
    limiter = get_limiter()

    email_key = f"rl:login:email:{email}:ip:{ip or 'unknown'}"
    ip_key = f"rl:login:ip:{ip or 'unknown'}"

    email_result = await limiter.hit(
        email_key, LOGIN_MAX_FAILURES_PER_EMAIL_IP, LOGIN_FAILURE_WINDOW_SECONDS
    )
    if not email_result.allowed:
        raise _locked_error(email_result.reset_at)

    ip_result = await limiter.hit(ip_key, LOGIN_MAX_PER_IP_PER_HOUR, 3600)
    if not ip_result.allowed:
        raise AppError(
            ErrorCode.AUTH_RATE_LIMITED,
            headers={"Retry-After": str(max(1, ip_result.reset_at - _now_ts()))},
        )

    user_row = await repo.get_user_by_email(db, email)
    if user_row is None or not verify_password(password, user_row["password_hash"]):
        await repo.insert_audit(
            db,
            workspace_id=None,
            user_id=user_row["id"] if user_row else None,
            action="user.login_failed",
            target=email,
            detail={"reason": "bad_credentials"},
            ip=ip,
        )
        raise AppError(ErrorCode.AUTH_INVALID_CREDENTIALS)

    if user_row["status"] == "disabled":
        raise AppError(ErrorCode.AUTH_ACCOUNT_DISABLED)
    if user_row["status"] == "deleted":
        raise AppError(ErrorCode.AUTH_INVALID_CREDENTIALS)

    await limiter.reset(email_key)

    await set_rls_context(db, None, user_row["id"])
    workspace_rows = await repo.list_user_workspaces(db, user_row["id"])
    if not workspace_rows:
        created = await _create_personal_workspace(db, user_id=user_row["id"])
        workspace_rows = [
            {**created, "role": "owner"},
        ]

    await set_rls_context(db, workspace_rows[0]["id"], user_row["id"])
    if current_session_id is not None:
        await repo.revoke_session(db, current_session_id)

    token, csrf, _expires_at = await _create_session(
        db, user_id=user_row["id"], active_workspace_id=workspace_rows[0]["id"]
    )

    await repo.insert_audit(
        db,
        workspace_id=workspace_rows[0]["id"],
        user_id=user_row["id"],
        action="user.login",
        target=str(user_row["id"]),
        detail={"active_workspace_id": str(workspace_rows[0]["id"])},
        ip=ip,
    )

    response = LoginResponse(
        user=_user_model(user_row),
        workspaces=[_workspace_model(w, w["role"]) for w in workspace_rows],
        active_workspace_id=workspace_rows[0]["id"],
        csrf_token=csrf,
    )
    return response, token, csrf


# ---------------------------------------------------------------------------
# 当前用户 / Workspace 切换
# ---------------------------------------------------------------------------


async def me(db: AsyncSession, *, user_id: UUID, active_workspace_id: UUID) -> MeResponse:
    await set_rls_context(db, active_workspace_id, user_id)
    rows = await repo.list_user_workspaces(db, user_id)
    active = next((r for r in rows if r["id"] == active_workspace_id), None)
    if active is None:
        raise AppError(ErrorCode.NOT_FOUND)
    user_row = await repo.get_user_by_id(db, user_id)
    if user_row is None:
        raise AppError(ErrorCode.NOT_FOUND)
    role = active["role"]
    capabilities = list(CAPABILITIES_BY_ROLE.get(role, ()))
    # `admin` 是平台级能力，与 workspace 角色正交（AC-54）。
    if bool(user_row.get("is_platform_admin", False)) and "admin" not in capabilities:
        capabilities.append("admin")
    return MeResponse(
        user=_user_model(user_row),
        workspaces=[_workspace_model(r, r["role"]) for r in rows],
        active_workspace_id=active_workspace_id,
        capabilities=capabilities,
        limits=_limits(),
    )


async def list_workspaces(db: AsyncSession, user_id: UUID) -> WorkspaceListResponse:
    await set_rls_context(db, None, user_id)
    rows = await repo.list_user_workspaces(db, user_id)
    return WorkspaceListResponse(items=[_workspace_model(r, r["role"]) for r in rows])


async def create_workspace(
    db: AsyncSession, *, user_id: UUID, name: str
) -> Workspace:
    await set_rls_context(db, None, user_id)
    if await repo.count_memberships(db, user_id) >= int(
        DEFAULT_LIMITS["max_workspaces_per_user"]  # type: ignore[arg-type]
    ):
        raise AppError(ErrorCode.QUOTA_EXCEEDED)
    row = await _create_personal_workspace(db, user_id=user_id, name=name)
    await repo.insert_audit(
        db,
        workspace_id=row["id"],
        user_id=user_id,
        action="workspace.created",
        target=str(row["id"]),
        detail={"name": name},
        ip=None,
    )
    return _workspace_model(row, "owner")


async def switch_workspace(
    db: AsyncSession, *, session_id: UUID, user_id: UUID, target_workspace_id: UUID
) -> WorkspaceSwitchResponse:
    await set_rls_context(db, None, user_id)
    membership = await repo.get_membership(
        db, workspace_id=target_workspace_id, user_id=user_id
    )
    if membership is None:
        # 非成员：对外统一 404（SEC-06），内部记 SECURITY_ABORT 审计（SEC-25）。
        await repo.insert_audit(
            db,
            workspace_id=None,
            user_id=user_id,
            action="security_abort",
            target=str(target_workspace_id),
            detail={"reason": "TENANT_LEAK", "external_status": 404},
        )
        raise AppError(ErrorCode.NOT_FOUND)

    await repo.set_active_workspace(db, session_id, target_workspace_id)
    await set_rls_context(db, target_workspace_id, user_id)
    return WorkspaceSwitchResponse(
        active_workspace_id=target_workspace_id, csrf_token=new_csrf_token()
    )


async def workspace_detail(
    db: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> WorkspaceDetail:
    await set_rls_context(db, workspace_id, user_id)
    ws = await repo.get_workspace(db, workspace_id)
    membership = await repo.get_membership(db, workspace_id=workspace_id, user_id=user_id)
    if ws is None or membership is None:
        raise AppError(ErrorCode.NOT_FOUND)
    settings_row = await repo.get_settings(db, workspace_id)
    counts = WorkspaceCounts(
        documents=await repo.count_documents(db, workspace_id),
        reviews=await repo.count_reviews(db, workspace_id),
        open_tasks=await repo.count_open_tasks(db, workspace_id),
    )
    summary = SettingsSummary(
        timezone=settings_row["timezone"] if settings_row else DEFAULT_TIMEZONE,
        daily_at=settings_row["daily_at"] if settings_row else time(1, 0),
        weekly_at=settings_row["weekly_at"] if settings_row else time(1, 30),
        yearly_at=settings_row["yearly_at"] if settings_row else time(2, 0),
        reviews_root=settings_row["reviews_root"] if settings_row else "wiki/复盘",
    )
    return WorkspaceDetail(
        workspace=_workspace_model(ws, membership["role"]),
        settings_summary=summary,
        counts=counts,
    )


async def rename_workspace(
    db: AsyncSession, *, workspace_id: UUID, user_id: UUID, name: str
) -> Workspace:
    await set_rls_context(db, workspace_id, user_id)
    await repo.rename_workspace(db, workspace_id, name)
    ws = await repo.get_workspace(db, workspace_id)
    membership = await repo.get_membership(db, workspace_id=workspace_id, user_id=user_id)
    if ws is None or membership is None:
        raise AppError(ErrorCode.NOT_FOUND)
    await repo.insert_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="workspace.renamed",
        target=str(workspace_id),
        detail={"name": name},
        ip=None,
    )
    return _workspace_model(ws, membership["role"])


# ---------------------------------------------------------------------------
# 会话销毁
# ---------------------------------------------------------------------------


async def logout(
    db: AsyncSession, *, session_id: UUID, user_id: UUID, workspace_id: UUID | None
) -> None:
    # audit_logs 的 INSERT 受 RLS 约束：先把上下文切回当前 workspace。
    await set_rls_context(db, workspace_id, user_id)
    await repo.revoke_session(db, session_id)
    await repo.insert_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="user.logout",
        target=str(user_id),
        detail={},
        ip=None,
    )


async def logout_all(
    db: AsyncSession, *, user_id: UUID, workspace_id: UUID | None, ip: str | None
) -> None:
    await set_rls_context(db, workspace_id, user_id)
    await repo.revoke_all_sessions(db, user_id)
    await repo.insert_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="user.logout_all",
        target=str(user_id),
        detail={},
        ip=ip,
    )


# ---------------------------------------------------------------------------
# 邀请码（平台管理员）
# ---------------------------------------------------------------------------


async def create_invite_codes(
    db: AsyncSession,
    *,
    admin_id: UUID,
    count: int,
    expires_in_days: int,
    note: str | None,
) -> list[dict[str, Any]]:
    now = utcnow()
    expires_at = now + timedelta(days=expires_in_days)
    codes = [(make_invite_code(), expires_at) for _ in range(count)]
    rows = await repo.insert_invite_codes(
        db, created_by=admin_id, codes=codes, note=note
    )
    await repo.insert_audit(
        db,
        workspace_id=None,
        user_id=admin_id,
        action="admin.invite_codes_created",
        target="invite_codes",
        detail={"count": count, "expires_in_days": expires_in_days},
        ip=None,
    )
    return rows
