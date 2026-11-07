"""导入服务：ZIP 上传、Git 连接、同步、重试、任务查询。"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any
from uuid import UUID

import aiofiles
from fastapi import UploadFile
from mylife_contract.errors import ErrorCode
from mylife_contract.ingest import (
    GitConnection,
    GitConnectionSummary,
    GitIngestRequest,
    IngestCounters,
    IngestFailure,
    IngestJob,
    IngestJobDetail,
    IngestJobList,
    IngestProgress,
    IngestSourcesResponse,
    SnapshotSummary,
    ZipImportSummary,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repo_phase1 as repo
from mylife_api.crypto import encrypt_credential
from mylife_api.errors import AppError
from mylife_api.ids import uuid7
from mylife_api.queue import enqueue_ingest
from mylife_api.repositories import insert_audit
from mylife_api.settings import settings

logger = logging.getLogger("mylife.api.ingest")

ACTIVE_INGEST_STATUSES = {"queued", "running"}


def _job_model(row: dict[str, Any]) -> IngestJob:
    progress = row.get("progress_json") or {}
    if isinstance(progress, str):
        progress = json.loads(progress)
    return IngestJob(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        progress=IngestProgress(**progress),
        snapshot_id=row.get("snapshot_id"),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


def _connection_model(row: dict[str, Any]) -> GitConnection:
    return GitConnection(
        id=row["id"],
        provider=row["provider"],
        repo_full_name=row["repo_full_name"],
        base_branch=row["base_branch"],
        mode=row["mode"] or "managed",
        reviews_root=row["reviews_root"],
        sync_interval_minutes=row["sync_interval_minutes"],
        last_sync_at=row.get("last_sync_at"),
        last_sync_status=row.get("last_sync_status"),
        credential_status="valid" if row.get("has_credential") else "untested",
    )


def _connection_summary(row: dict[str, Any]) -> GitConnectionSummary:
    return GitConnectionSummary(
        id=row["id"],
        provider=row["provider"],
        repo_full_name=row["repo_full_name"],
        base_branch=row["base_branch"],
        mode=row["mode"] or "managed",
        reviews_root=row["reviews_root"],
        sync_interval_minutes=row["sync_interval_minutes"],
        last_sync_at=row.get("last_sync_at"),
        last_sync_status=row.get("last_sync_status"),
        credential_status="valid" if row.get("has_credential") else "untested",
    )


async def _assert_no_active(session: AsyncSession, workspace_id: UUID) -> None:
    if await repo.count_active_ingest(session, workspace_id) >= 1:
        raise AppError(
            ErrorCode.INGEST_CONCURRENCY_LIMIT,
            headers={"Retry-After": "30"},
        )


async def _save_upload(upload: UploadFile, *, workspace_id: UUID) -> tuple[str, str]:
    """流式落盘到数据根，返回 (绝对路径, sha256)。超过上限 → 413。"""
    target_dir = (
        Path(settings.data_root) / "workspaces" / str(workspace_id) / "ingest" / str(uuid7())
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "source.zip"

    digest = hashlib.sha256()
    written = 0
    try:
        async with aiofiles.open(target, "wb") as sink:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > settings.max_zip_bytes:
                    raise AppError(ErrorCode.PAYLOAD_TOO_LARGE)
                digest.update(chunk)
                await sink.write(chunk)
    except AppError:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise AppError(ErrorCode.INTERNAL_ERROR) from exc

    if written == 0 or not zipfile.is_zipfile(target):
        shutil.rmtree(target_dir, ignore_errors=True)
        raise AppError(ErrorCode.INGEST_ZIP_INVALID)
    return str(target), digest.hexdigest()


def _job_detail(row: dict[str, Any]) -> IngestJobDetail:
    counters_raw = row.get("counters_json") or {}
    if isinstance(counters_raw, str):
        counters_raw = json.loads(counters_raw)
    failed_raw = row.get("failed_items_json") or []
    if isinstance(failed_raw, str):
        failed_raw = json.loads(failed_raw)

    snapshot = None
    if row.get("snapshot_id") and row.get("manifest_digest"):
        snapshot = SnapshotSummary(
            snapshot_id=row["snapshot_id"],
            commit_sha=row.get("commit_sha"),
            doc_count=row.get("doc_count") or 0,
            as_of=row.get("as_of") or row["created_at"],
            manifest_digest=row["manifest_digest"],
        )
    return IngestJobDetail(
        job=_job_model(row),
        counters=IngestCounters(**counters_raw),
        failed_items=[IngestFailure(**item) for item in failed_raw],
        snapshot=snapshot,
    )


async def create_zip_job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    upload: UploadFile,
    mode: str,
    reviews_root: str,
    ip: str | None,
) -> IngestJob:
    await _assert_no_active(session, workspace_id)
    path, sha = await _save_upload(upload, workspace_id=workspace_id)

    existing = await repo.find_zip_job_by_sha(session, workspace_id, sha)
    if existing is not None:
        shutil.rmtree(Path(path).parent, ignore_errors=True)
        return _job_model(existing)

    row = await repo.insert_job(
        session,
        workspace_id=workspace_id,
        kind="zip",
        payload={
            "source": "zip",
            "zip_path": path,
            "zip_sha256": sha,
            "mode": mode,
            "reviews_root": reviews_root,
        },
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="ingest.started",
        target=str(row["id"]),
        detail={"kind": "zip", "zip_sha256": sha},
        ip=ip,
    )
    enqueue_ingest("zip", str(row["id"]), str(workspace_id))
    return _job_model(row)


async def create_git_connection(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    payload: GitIngestRequest,
    ip: str | None,
) -> tuple[dict[str, Any], GitConnection]:
    ciphertext = None
    if payload.credential:
        ciphertext = encrypt_credential(
            payload.credential, workspace_id=str(workspace_id), provider="git"
        )

    await _assert_no_active(session, workspace_id)
    try:
        connection = await repo.insert_git_connection(
            session,
            workspace_id=workspace_id,
            provider=payload.provider,
            repo_full_name=payload.repo_full_name,
            base_branch=payload.base_branch,
            auth_type=payload.auth_type,
            credential_ciphertext=ciphertext,
            reviews_root=payload.reviews_root,
            mode=payload.mode,
            sync_interval_minutes=payload.sync_interval_minutes,
        )
    except IntegrityError as exc:
        raise AppError(ErrorCode.CONFLICT) from exc

    job_row = await repo.insert_job(
        session,
        workspace_id=workspace_id,
        kind="git",
        payload={"source": "git", "connection_id": str(connection["id"])},
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="ingest.started",
        target=str(job_row["id"]),
        detail={"kind": "git", "provider": payload.provider},
        ip=ip,
    )
    if payload.initial_sync:
        enqueue_ingest("git", str(job_row["id"]), str(workspace_id))
    return job_row, _connection_model(connection)


async def create_sync_job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    connection_id: UUID,
    force_refresh: bool,
    ip: str | None,
) -> IngestJob:
    connection = await repo.get_git_connection(session, workspace_id, connection_id)
    if connection is None:
        raise AppError(ErrorCode.NOT_FOUND)

    active = await repo.count_active_ingest(session, workspace_id)
    if active:
        raise AppError(ErrorCode.INGEST_CONCURRENCY_LIMIT, headers={"Retry-After": "30"})

    row = await repo.insert_job(
        session,
        workspace_id=workspace_id,
        kind="sync",
        payload={
            "source": "git",
            "connection_id": str(connection_id),
            "force_refresh": force_refresh,
        },
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="ingest.started",
        target=str(row["id"]),
        detail={"kind": "sync", "connection_id": str(connection_id)},
        ip=ip,
    )
    enqueue_ingest("git", str(row["id"]), str(workspace_id))
    return _job_model(row)


async def retry_job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    job_id: UUID,
    only_failed: bool,
    ip: str | None,
) -> IngestJob:
    original = await repo.get_job(session, workspace_id, job_id)
    if original is None:
        raise AppError(ErrorCode.INGEST_JOB_NOT_FOUND)
    if original["status"] not in {"failed", "partial"}:
        raise AppError(ErrorCode.INGEST_JOB_NOT_RETRYABLE)
    await _assert_no_active(session, workspace_id)

    payload = original.get("payload_json") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload = dict(payload)
    payload["only_failed"] = only_failed

    row = await repo.insert_job(
        session,
        workspace_id=workspace_id,
        kind="resync",
        payload=payload,
        parent_job_id=original["id"],
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="ingest.retried",
        target=str(row["id"]),
        detail={"parent_job_id": str(original["id"]), "only_failed": only_failed},
        ip=ip,
    )
    enqueue_ingest("zip" if payload.get("zip_path") else "git", str(row["id"]), str(workspace_id))
    return _job_model(row)


async def get_job_detail(
    session: AsyncSession, *, workspace_id: UUID, job_id: UUID
) -> IngestJobDetail:
    row = await repo.get_job(session, workspace_id, job_id)
    if row is None:
        raise AppError(ErrorCode.INGEST_JOB_NOT_FOUND)
    return _job_detail(row)


async def list_jobs(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    status_filter: str | None,
    kind: str | None,
    limit: int,
) -> IngestJobList:
    rows = await repo.list_jobs(
        session, workspace_id, status_filter=status_filter, kind=kind, limit=limit
    )
    return IngestJobList(items=[_job_model(r) for r in rows], next_cursor=None, has_more=False)


async def list_sources(session: AsyncSession, *, workspace_id: UUID) -> IngestSourcesResponse:
    connections = await repo.list_git_connections(session, workspace_id)
    zip_rows = await repo.list_zip_imports(session, workspace_id)
    return IngestSourcesResponse(
        git_connections=[_connection_summary(r) for r in connections],
        zip_imports=[
            ZipImportSummary(
                job_id=r["id"],
                status=r["status"],
                created_at=r["created_at"],
                finished_at=r.get("finished_at"),
            )
            for r in zip_rows
        ],
    )


async def create_scheduled_sync(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    connection_id: UUID,
    interval_minutes: int,
) -> dict[str, Any]:
    """dispatcher 到点触发的定时拉取（AC-52；PHASE4-SPEC D26/D64）。

    返回值 `{"status": "queued"|"skipped", "job_id": ...}`；任何异常都不抛出，
    以免单连接失败拖垮整个 tick。
    """
    from datetime import UTC, datetime, timedelta

    next_sync = (
        datetime.now(UTC) + timedelta(minutes=interval_minutes)
        if interval_minutes > 0
        else None
    )
    active = await repo.count_active_ingest(session, workspace_id)
    if active:
        await session.execute(
            text(
                "UPDATE git_connections SET last_sync_status = 'skipped', "
                "  next_sync_at = :next_sync, updated_at = now() "
                "WHERE workspace_id = :ws AND id = :id"
            ),
            {"next_sync": next_sync, "ws": workspace_id, "id": connection_id},
        )
        await session.commit()
        return {"status": "skipped", "job_id": None}
    row = await repo.insert_job(
        session,
        workspace_id=workspace_id,
        kind="sync",
        payload={"source": "git", "connection_id": str(connection_id)},
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=None,
        action="ingest.scheduled_sync",
        target=str(row["id"]),
        detail={"connection_id": str(connection_id)},
    )
    enqueue_ingest("git", str(row["id"]), str(workspace_id))
    await session.execute(
        text(
            "UPDATE git_connections SET last_sync_at = now(), last_sync_status = 'ok', "
            "  next_sync_at = :next_sync, updated_at = now() "
            "WHERE workspace_id = :ws AND id = :id"
        ),
        {"next_sync": next_sync, "ws": workspace_id, "id": connection_id},
    )
    await session.commit()
    return {"status": "queued", "job_id": str(row["id"])}
