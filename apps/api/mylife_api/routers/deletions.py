"""删除冷却路由（API-CONTRACTS §3.11.4–§3.11.7；SEC-27）。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from mylife_contract.deletions import DeletionCreate, DeletionRequest
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import services_deletion
from mylife_api.deps import RunContext, get_db, get_run_context

router = APIRouter(tags=["deletions"])


@router.post(
    "/deletions",
    response_model=DeletionRequest,
    status_code=status.HTTP_201_CREATED,
)
async def create_deletion(
    payload: DeletionCreate,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> DeletionRequest:
    data = await services_deletion.create_deletion(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        role=ctx.role,
        scope=payload.scope,
        confirm_text=payload.confirm_text,
        reason=payload.reason,
        skip_export=payload.skip_export,
    )
    return DeletionRequest(**data)


@router.get("/deletions/current")
async def current_deletion(
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> Any:
    return await services_deletion.current_deletion(db, workspace_id=ctx.workspace_id)


@router.post("/deletions/{deletion_id}/cancel")
async def cancel_deletion(
    deletion_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    return await services_deletion.cancel_deletion(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        role=ctx.role,
        deletion_id=deletion_id,
    )
