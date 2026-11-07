"""交付路由（API-CONTRACTS §3.7.10–§3.7.12；PHASE4-SPEC D37）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from mylife_contract.delivery import (
    DeliveryDetail,
    DeliveryList,
    DeliveryRetryResponse,
)
from mylife_contract.errors import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import services_delivery
from mylife_api.deps import RunContext, get_db, get_run_context
from mylife_api.errors import AppError

router = APIRouter(tags=["deliveries"])


@router.get("/deliveries", response_model=DeliveryList)
async def list_deliveries(
    review_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> DeliveryList:
    return await services_delivery.list_delivery_records(
        db,
        workspace_id=ctx.workspace_id,
        review_id=review_id,
        status=status_filter,
        limit=limit,
    )


@router.get("/deliveries/{delivery_id}", response_model=DeliveryDetail)
async def get_delivery(
    delivery_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> DeliveryDetail:
    return await services_delivery.get_delivery_detail(
        db, workspace_id=ctx.workspace_id, delivery_id=delivery_id
    )


@router.post(
    "/deliveries/{delivery_id}/retry",
    response_model=DeliveryRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_delivery(
    delivery_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> DeliveryRetryResponse:
    result = await services_delivery.retry_delivery(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        role=ctx.role,
        delivery_id=delivery_id,
    )
    return DeliveryRetryResponse(**result)


@router.get("/deliveries/{delivery_id}/download")
async def download_delivery(
    delivery_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """对象存储降级交付的 ZIP 下载（AC-39 等效证据）。"""
    detail = await services_delivery.get_delivery_detail(
        db, workspace_id=ctx.workspace_id, delivery_id=delivery_id
    )
    path = services_delivery.object_zip_path(
        workspace_id=ctx.workspace_id, delivery_id=delivery_id
    )
    if detail.delivery.target != "object_store" or not path.exists():
        raise AppError(ErrorCode.DELIVERY_NOT_FOUND)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"mylife-delivery-{delivery_id}.zip",
    )
