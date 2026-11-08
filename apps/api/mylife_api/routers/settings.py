"""设置路由（API-CONTRACTS §3.9.1–§3.9.11；AC-49/AC-50/AC-51/AC-52/AC-53）。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response, status
from mylife_contract.errors import ErrorCode
from mylife_contract.settings import (
    GitConnectionList,
    GitSettingsUpdate,
    LlmSettings,
    LlmTestRequest,
    LlmTestResult,
    LlmUpdate,
    RetrievalSettings,
    RetrievalUpdate,
    ScheduleSettings,
    ScheduleUpdate,
    UsageResponse,
)
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import services_llm, services_settings
from mylife_api.deps import RunContext, get_db, get_run_context
from mylife_api.errors import AppError

router = APIRouter(prefix="/settings", tags=["settings"])


# ---------------------------------------------------------------------------
# LLM（§3.9.1–§3.9.3；AC-49）
# ---------------------------------------------------------------------------


@router.get("/llm", response_model=LlmSettings)
async def get_llm(
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> LlmSettings:
    return await services_llm.get_llm_settings(db, workspace_id=ctx.workspace_id)


@router.put("/llm", response_model=LlmSettings)
async def put_llm(
    payload: LlmUpdate,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> LlmSettings:
    return await services_llm.put_llm_settings(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        role=ctx.role,
        payload=payload,
    )


@router.post("/llm/test", response_model=LlmTestResult)
async def test_llm(
    payload: LlmTestRequest,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> LlmTestResult:
    return await services_llm.test_llm(
        db,
        workspace_id=ctx.workspace_id,
        provider=payload.provider,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
    )


# ---------------------------------------------------------------------------
# Embedding（§3.9.4–§3.9.6；AC-50）在 C5 接入
# ---------------------------------------------------------------------------


@router.get("/schedule", response_model=ScheduleSettings)
async def get_schedule(
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ScheduleSettings:
    return await services_settings.get_schedule(db, workspace_id=ctx.workspace_id)


@router.put("/schedule", response_model=ScheduleSettings)
async def put_schedule(
    payload: ScheduleUpdate,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ScheduleSettings:
    return await services_settings.put_schedule(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        role=ctx.role,
        payload=payload,
    )


@router.get("/git", response_model=GitConnectionList)
async def list_git(
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> GitConnectionList:
    rows = await services_settings.list_git_connections(
        db, workspace_id=ctx.workspace_id
    )
    return GitConnectionList(items=rows)


@router.put("/git", response_model=dict)
async def put_git(
    payload: GitSettingsUpdate,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    summary = await services_settings.put_git_connection(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        role=ctx.role,
        payload=payload,
    )
    return summary.model_dump(mode="json")


@router.delete("/git", status_code=status.HTTP_204_NO_CONTENT)
async def delete_git(
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await services_settings.delete_git_connection(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        role=ctx.role,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    from_ts: datetime = Query(alias="from"),
    to_ts: datetime = Query(alias="to"),
    group_by: str = Query(default="day", pattern="^(day|kind|model)$"),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> UsageResponse:
    if from_ts.tzinfo is None:
        from_ts = from_ts.replace(tzinfo=UTC)
    if to_ts.tzinfo is None:
        to_ts = to_ts.replace(tzinfo=UTC)
    if from_ts > to_ts:
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "from"})
    if (to_ts - from_ts).days > 366:
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "to"})
    data = await services_settings.get_usage(
        db,
        workspace_id=ctx.workspace_id,
        from_ts=from_ts,
        to_ts=to_ts,
        group_by=group_by,
    )
    return UsageResponse(**data)


@router.get("/retrieval", response_model=RetrievalSettings)
async def get_retrieval(
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> RetrievalSettings:
    return await services_settings.get_retrieval(db, workspace_id=ctx.workspace_id)


@router.put("/retrieval", response_model=RetrievalSettings)
async def put_retrieval(
    payload: RetrievalUpdate,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> RetrievalSettings:
    return await services_settings.put_retrieval(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        role=ctx.role,
        payload=payload,
    )
