"""admin 路由（API-CONTRACTS §3.10；AC-54 / SEC-30）。

所有端点双重校验（`require_platform_admin`）、受限流（100 次/分钟），并写审计。
**这里没有任何返回笔记/复盘/对话正文的端点**——只有元数据与聚合（SEC-30）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from mylife_contract.admin import (
    AdminAuditLogList,
    AdminSystemConfigItem,
    AdminSystemConfigList,
    AdminSystemConfigUpdate,
    AdminUsageResponse,
    AdminUser,
    AdminUserDisableRequest,
    AdminUserListResponse,
    AdminWorkspace,
    AdminWorkspaceListResponse,
    InviteCode,
    InviteCodeCreated,
    InviteCodeCreateRequest,
    InviteCodeCreateResponse,
    InviteCodeListResponse,
)
from mylife_contract.errors import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories as repo
from mylife_api import services, services_admin
from mylife_api.deps import AuthState, client_ip, get_db, require_platform_admin
from mylife_api.errors import AppError

router = APIRouter(prefix="/admin", tags=["admin"])


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, details={"reason": "CURSOR_INVALID"}
        ) from exc


def _next_cursor(offset: int, limit: int, total: int) -> str | None:
    return str(offset + limit) if offset + limit < total else None


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    q: str | None = Query(default=None, max_length=200),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserListResponse:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    offset = _offset(cursor)
    items, total = await services_admin.list_users(
        db, q=q, status=status_filter, limit=limit, offset=offset
    )
    return AdminUserListResponse(
        items=items, next_cursor=_next_cursor(offset, limit, total)
    )


@router.get("/users/{user_id}", response_model=AdminUser)
async def get_user(
    user_id: UUID,
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    return await services_admin.get_user(db, user_id=user_id)


@router.post("/users/{user_id}/disable", response_model=AdminUser)
async def disable_user(
    user_id: UUID,
    payload: AdminUserDisableRequest,
    request: Request,
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    result = await services_admin.disable_user(
        db,
        admin_id=admin.user_id,
        user_id=user_id,
        reason=payload.reason,
        ip=client_ip(request),
    )
    await db.commit()
    return result


@router.post("/users/{user_id}/enable", response_model=AdminUser)
async def enable_user(
    user_id: UUID,
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    result = await services_admin.enable_user(
        db, admin_id=admin.user_id, user_id=user_id
    )
    await db.commit()
    return result


@router.get("/workspaces", response_model=AdminWorkspaceListResponse)
async def list_workspaces(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminWorkspaceListResponse:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    offset = _offset(cursor)
    items, total = await services_admin.list_workspaces(
        db, q=q, limit=limit, offset=offset
    )
    return AdminWorkspaceListResponse(
        items=items, next_cursor=_next_cursor(offset, limit, total)
    )


@router.get("/workspaces/{workspace_id}", response_model=AdminWorkspace)
async def get_workspace(
    workspace_id: UUID,
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminWorkspace:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    return await services_admin.get_workspace(db, workspace_id=workspace_id)


@router.post(
    "/invite-codes",
    response_model=InviteCodeCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite_codes(
    payload: InviteCodeCreateRequest,
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteCodeCreateResponse:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    if payload.count > 50:
        raise AppError(ErrorCode.ADMIN_INVITE_LIMIT)
    rows = await services.create_invite_codes(
        db,
        admin_id=admin.user_id,
        count=payload.count,
        expires_in_days=payload.expires_in_days,
        note=payload.note,
    )
    return InviteCodeCreateResponse(
        items=[InviteCodeCreated(code=r["code"], expires_at=r["expires_at"]) for r in rows]
    )


@router.get("/invite-codes", response_model=InviteCodeListResponse)
async def list_invite_codes(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteCodeListResponse:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    if status_filter not in {None, "unused", "used", "expired"}:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details={"field": "status", "reason": "INVALID_VALUE"},
        )
    rows = await repo.list_invite_codes(db, status=status_filter, limit=limit)
    return InviteCodeListResponse(
        items=[
            InviteCode(
                id=r["id"],
                code=r["code"],
                created_by=r["created_by"],
                used_by=r["used_by"],
                expires_at=r["expires_at"],
                used_at=r["used_at"],
                note=r["note"],
                status=r["status"],
            )
            for r in rows
        ]
    )


@router.delete("/invite-codes/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invite_code(
    code: str,
    request: Request,
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    await services_admin.revoke_invite_code(
        db, admin_id=admin.user_id, code=code, ip=client_ip(request)
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit-logs", response_model=AdminAuditLogList)
async def list_audit_logs(
    workspace_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None, max_length=100),
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAuditLogList:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    if from_ts is not None and from_ts.tzinfo is None:
        from_ts = from_ts.replace(tzinfo=UTC)
    if to_ts is not None and to_ts.tzinfo is None:
        to_ts = to_ts.replace(tzinfo=UTC)
    offset = _offset(cursor)
    items, total = await services_admin.list_audit_logs(
        db,
        action=action,
        workspace_id=workspace_id,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
        offset=offset,
    )
    return AdminAuditLogList(items=items, next_cursor=_next_cursor(offset, limit, total))


@router.get("/system-config", response_model=AdminSystemConfigList)
async def get_system_config(
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminSystemConfigList:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    return AdminSystemConfigList(items=await services_admin.list_system_config(db))


@router.put("/system-config", response_model=AdminSystemConfigItem)
async def put_system_config(
    payload: AdminSystemConfigUpdate,
    request: Request,
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminSystemConfigItem:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    item = await services_admin.update_system_config(
        db,
        admin_id=admin.user_id,
        key=payload.key,
        value=payload.value,
        ip=client_ip(request),
    )
    await db.commit()
    return item


@router.get("/usage", response_model=AdminUsageResponse)
async def get_usage(
    from_ts: datetime = Query(alias="from"),
    to_ts: datetime = Query(alias="to"),
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUsageResponse:
    await services_admin.enforce_admin_rate_limit(admin.user_id)
    if from_ts.tzinfo is None:
        from_ts = from_ts.replace(tzinfo=UTC)
    if to_ts.tzinfo is None:
        to_ts = to_ts.replace(tzinfo=UTC)
    if from_ts > to_ts:
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "from"})
    return await services_admin.usage(db, from_ts=from_ts, to_ts=to_ts)
