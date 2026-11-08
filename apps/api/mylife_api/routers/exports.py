"""导出路由（API-CONTRACTS §3.11.1–§3.11.3；AC-55 / AC-64）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import FileResponse
from mylife_contract.errors import ErrorCode
from mylife_contract.exports import ExportCreate, ExportCreated, ExportJob
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import services_export
from mylife_api.deps import RunContext, get_db, get_run_context
from mylife_api.errors import AppError
from mylife_api.repositories import insert_audit
from mylife_api.settings import settings as app_settings

router = APIRouter(tags=["exports"])


@router.post("/exports", response_model=ExportCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_export(
    payload: ExportCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ExportCreated:
    created = await services_export.create_export(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        role=ctx.role,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    # 同步构建（数据量受工作区限制）；失败落 `failed`，不抛出 500。
    try:
        await services_export.build_export(
            db, workspace_id=ctx.workspace_id, export_id=created.export_id
        )
    except AppError:
        await services_export.fail_export(
            db, workspace_id=ctx.workspace_id, export_id=created.export_id,
            reason="EXPORT_BUILD_FAILED",
        )
    await db.commit()
    return created


@router.get("/exports/{export_id}", response_model=ExportJob)
async def get_export(
    export_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ExportJob:
    return await services_export.get_export(
        db, workspace_id=ctx.workspace_id, export_id=export_id
    )


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    job = await services_export.get_export(
        db, workspace_id=ctx.workspace_id, export_id=export_id
    )
    if job.status in {"queued", "running"}:
        raise AppError(ErrorCode.EXPORT_NOT_READY)
    if job.status == "expired":
        raise AppError(ErrorCode.EXPORT_EXPIRED)
    if job.status != "ready":
        raise AppError(ErrorCode.EXPORT_NOT_READY, details={"status": job.status})

    path = services_export.artifact_path(
        app_settings.data_root, ctx.workspace_id, export_id
    )
    if not path.exists():
        raise AppError(ErrorCode.EXPORT_EXPIRED)

    await insert_audit(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        action="export.downloaded",
        target=str(export_id),
        detail={},
    )
    workspace = (
        await db.execute(
            text("SELECT slug FROM workspaces WHERE id = :ws"),
            {"ws": ctx.workspace_id},
        )
    ).scalar_one_or_none()
    filename = f"mylife-{workspace or 'workspace'}-{export_id}.zip"
    return FileResponse(path, media_type="application/zip", filename=filename)
