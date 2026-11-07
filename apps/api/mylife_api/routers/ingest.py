"""ingest 路由（API-CONTRACTS §3.3）。"""

from __future__ import annotations

import time
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from mylife_contract.errors import ErrorCode
from mylife_contract.ingest import (
    GitIngestRequest,
    GitIngestResponse,
    IngestJob,
    IngestJobDetail,
    IngestJobList,
    IngestRetryRequest,
    IngestSourcesResponse,
    IngestSyncRequest,
    ZipIngestResponse,
)
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import services_ingest
from mylife_api.deps import RunContext, client_ip, get_db, get_run_context, require_owner
from mylife_api.errors import AppError
from mylife_api.ratelimit import get_limiter

router = APIRouter(prefix="/ingest", tags=["ingest"])

INGEST_RATE_LIMIT = 20
INGEST_RATE_WINDOW = 3600


async def _rate_limit_ingest(ctx: RunContext) -> None:
    result = await get_limiter().hit(
        f"rl:ingest:{ctx.workspace_id}", INGEST_RATE_LIMIT, INGEST_RATE_WINDOW
    )
    if not result.allowed:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            headers={"Retry-After": str(max(1, result.reset_at - int(time.time())))},
        )


@router.post(
    "/zip",
    response_model=ZipIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_zip(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("managed"),
    reviews_root: str = Form("wiki/复盘"),
    ctx: RunContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> ZipIngestResponse:
    await _rate_limit_ingest(ctx)
    if mode not in {"managed", "vault_writeback", "separate"}:
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "mode"})
    if ".." in reviews_root.split("/") or reviews_root.startswith("/"):
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "reviews_root"})
    job = await services_ingest.create_zip_job(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        upload=file,
        mode=mode,
        reviews_root=reviews_root,
        ip=client_ip(request),
    )
    return ZipIngestResponse(job=job)


@router.post(
    "/git",
    response_model=GitIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_git(
    payload: GitIngestRequest,
    request: Request,
    ctx: RunContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> GitIngestResponse:
    await _rate_limit_ingest(ctx)
    job_row, connection = await services_ingest.create_git_connection(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        payload=payload,
        ip=client_ip(request),
    )
    return GitIngestResponse(
        job=services_ingest._job_model(job_row),  # noqa: SLF001 - 同域序列化
        connection=connection,
    )


@router.post(
    "/sync",
    response_model=IngestJob,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_sync(
    payload: IngestSyncRequest,
    request: Request,
    ctx: RunContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> IngestJob:
    await _rate_limit_ingest(ctx)
    return await services_ingest.create_sync_job(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        connection_id=payload.connection_id,
        force_refresh=payload.force_refresh,
        ip=client_ip(request),
    )


@router.get("/jobs", response_model=IngestJobList)
async def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> IngestJobList:
    return await services_ingest.list_jobs(
        db, workspace_id=ctx.workspace_id, status_filter=status_filter, kind=kind, limit=limit
    )


@router.get("/jobs/{job_id}", response_model=IngestJobDetail)
async def get_job(
    job_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> IngestJobDetail:
    return await services_ingest.get_job_detail(
        db, workspace_id=ctx.workspace_id, job_id=job_id
    )


@router.post(
    "/jobs/{job_id}/retry",
    response_model=IngestJob,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_job(
    job_id: UUID,
    payload: IngestRetryRequest,
    request: Request,
    ctx: RunContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> IngestJob:
    return await services_ingest.retry_job(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        job_id=job_id,
        only_failed=payload.only_failed,
        ip=client_ip(request),
    )


@router.get("/sources", response_model=IngestSourcesResponse)
async def list_sources(
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> IngestSourcesResponse:
    return await services_ingest.list_sources(db, workspace_id=ctx.workspace_id)
