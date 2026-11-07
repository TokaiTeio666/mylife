"""复盘路由（API-CONTRACTS §3.7.1–§3.7.9）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from mylife_contract.review import (
    ApproveRequest,
    GateDecisionResponse,
    RejectRequest,
    ReviewCreateRequest,
    ReviewCreateResponse,
    ReviewDetail,
    ReviewList,
)
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import services_review
from mylife_api.deps import RunContext, get_db, get_run_context

router = APIRouter(tags=["reviews"])


@router.post(
    "/reviews",
    response_model=ReviewCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_review(
    payload: ReviewCreateRequest,
    response: Response,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ReviewCreateResponse:
    result, http_status = await services_review.create_review(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        role=ctx.role,
        payload=payload,
    )
    response.status_code = http_status
    return result


@router.get("/reviews", response_model=ReviewList)
async def list_reviews(
    kind: str | None = Query(default=None, pattern="^(daily|weekly|yearly)$"),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ReviewList:
    return await services_review.list_reviews(
        db,
        workspace_id=ctx.workspace_id,
        kind=kind,
        status=status_filter,
        limit=limit,
        cursor=cursor,
    )


@router.get("/reviews/{review_id}", response_model=ReviewDetail)
async def get_review(
    review_id: UUID,
    version: int | None = Query(default=None, ge=1),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ReviewDetail:
    return await services_review.get_review_detail(
        db, workspace_id=ctx.workspace_id, review_id=review_id, version=version
    )


@router.get("/reviews/{review_id}/versions/{version}", response_model=ReviewDetail)
async def get_review_version(
    review_id: UUID,
    version: int,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ReviewDetail:
    return await services_review.get_review_detail(
        db, workspace_id=ctx.workspace_id, review_id=review_id, version=version
    )


@router.post(
    "/runs/{run_id}/approve",
    response_model=GateDecisionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def approve_run(
    run_id: UUID,
    payload: ApproveRequest,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> GateDecisionResponse:
    result = await services_review.approve_run(
        db,
        workspace_id=ctx.workspace_id,
        run_id=run_id,
        user_id=ctx.user_id,
        role=ctx.role,
        note=payload.decision_note,
        edited_content_md=payload.edited_content_md,
    )
    return GateDecisionResponse(**result)


@router.post(
    "/runs/{run_id}/reject",
    response_model=GateDecisionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reject_run(
    run_id: UUID,
    payload: RejectRequest,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> GateDecisionResponse:
    result = await services_review.reject_run(
        db,
        workspace_id=ctx.workspace_id,
        run_id=run_id,
        user_id=ctx.user_id,
        role=ctx.role,
        reason_code=payload.reason_code,
        note=payload.note,
    )
    return GateDecisionResponse(**result)


