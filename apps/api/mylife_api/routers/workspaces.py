"""workspaces 路由（API-CONTRACTS §3.2）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from mylife_contract.auth import Workspace
from mylife_contract.errors import ErrorCode
from mylife_contract.workspace import (
    WorkspaceCreateRequest,
    WorkspaceDetail,
    WorkspaceListResponse,
    WorkspaceMember,
    WorkspaceMembersResponse,
    WorkspacePatchRequest,
    WorkspaceSwitchResponse,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import services
from mylife_api.deps import AuthState, RunContext, get_db, get_run_context, require_session
from mylife_api.errors import AppError
from mylife_api.security import csrf_cookie_kwargs

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

_MAX_AGE = 30 * 24 * 3600


@router.get("", response_model=WorkspaceListResponse)
async def list_workspaces(
    auth: AuthState = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceListResponse:
    return await services.list_workspaces(db, auth.user_id)


@router.post("", response_model=Workspace, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreateRequest,
    auth: AuthState = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> Workspace:
    return await services.create_workspace(db, user_id=auth.user_id, name=payload.name)


@router.post("/{workspace_id}/switch", response_model=WorkspaceSwitchResponse)
async def switch_workspace(
    workspace_id: UUID,
    response: Response,
    auth: AuthState = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceSwitchResponse:
    result = await services.switch_workspace(
        db, session_id=auth.session_id, user_id=auth.user_id, target_workspace_id=workspace_id
    )
    response.set_cookie(value=result.csrf_token, **csrf_cookie_kwargs(max_age=_MAX_AGE))  # type: ignore[arg-type]
    return result


@router.get("/{workspace_id}", response_model=WorkspaceDetail)
async def get_workspace(
    workspace_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceDetail:
    _assert_active(ctx, workspace_id)
    return await services.workspace_detail(
        db, workspace_id=workspace_id, user_id=ctx.user_id
    )


@router.patch("/{workspace_id}", response_model=Workspace)
async def patch_workspace(
    workspace_id: UUID,
    payload: WorkspacePatchRequest,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> Workspace:
    _assert_active(ctx, workspace_id)
    if ctx.role != "owner":
        raise AppError(ErrorCode.FORBIDDEN)
    if payload.name is None:
        row = await services.workspace_detail(
            db, workspace_id=workspace_id, user_id=ctx.user_id
        )
        return row.workspace
    return await services.rename_workspace(
        db, workspace_id=workspace_id, user_id=ctx.user_id, name=payload.name
    )


@router.get("/{workspace_id}/members", response_model=WorkspaceMembersResponse)
async def list_members(
    workspace_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceMembersResponse:
    _assert_active(ctx, workspace_id)
    result = await db.execute(
        text(
            "SELECT user_id, role, joined_at FROM workspace_members "
            "WHERE workspace_id = :ws ORDER BY joined_at ASC"
        ),
        {"ws": workspace_id},
    )
    items = [
        WorkspaceMember(user_id=r.user_id, role=r.role, joined_at=r.joined_at)
        for r in result
    ]
    return WorkspaceMembersResponse(items=items)


def _assert_active(ctx: RunContext, workspace_id: UUID) -> None:
    """路径里的 workspace 必须等于会话当前 workspace；否则统一 404（SEC-06）。"""
    if ctx.workspace_id != workspace_id:
        raise AppError(ErrorCode.NOT_FOUND)
