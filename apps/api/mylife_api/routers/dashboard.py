"""仪表盘路由（API-CONTRACTS §3.8；AC-45…AC-47）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from mylife_contract.dashboard import (
    CalendarResponse,
    DashboardLatest,
    DashboardStats,
    TaskList,
)
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import services_dashboard
from mylife_api.deps import RunContext, get_db, get_run_context

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/latest", response_model=DashboardLatest)
async def latest(
    timezone: str | None = Query(default=None, max_length=64),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> DashboardLatest:
    data = await services_dashboard.get_latest(
        db, workspace_id=ctx.workspace_id, timezone=timezone
    )
    return DashboardLatest(**{key: data[key] for key in DashboardLatest.model_fields})


@router.get("/tasks", response_model=TaskList)
async def tasks(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> TaskList:
    wanted = [item.strip() for item in status.split(",") if item.strip()] if status else None
    data = await services_dashboard.tasks(
        db, workspace_id=ctx.workspace_id, status=wanted, limit=limit
    )
    return TaskList(**data)


@router.get("/calendar", response_model=CalendarResponse)
async def calendar_view(
    year: int = Query(ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> CalendarResponse:
    data = await services_dashboard.calendar_view(
        db, workspace_id=ctx.workspace_id, year=year, month=month
    )
    return CalendarResponse(**data)


@router.get("/stats", response_model=DashboardStats)
async def stats(
    range_key: str = Query(default="30d", alias="range", pattern="^(7d|30d|90d|year)$"),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> DashboardStats:
    data = await services_dashboard.stats(
        db, workspace_id=ctx.workspace_id, range_key=range_key
    )
    return DashboardStats(**data)
