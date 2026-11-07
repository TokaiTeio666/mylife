"""导入任务执行（ZIP / Git）。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from mylife_retrieval.embedding import EmbeddingUnavailable, build_provider
from mylife_runtime.crypto import CredentialDecryptError, decrypt_credential
from sqlalchemy import text

from mylife_worker.config import settings
from mylife_worker.db import bind_workspace, job_session
from mylife_worker.git_source import GitRejected, clone_repository
from mylife_worker.ignore import IgnoreMatcher, is_markdown
from mylife_worker.indexer import SourceFile, ingest_files
from mylife_worker.zip_source import ZipRejected, extract_zip

logger = logging.getLogger("mylife.worker.jobs")

GIT_HOSTS = {"github": "github.com", "gitlab": "gitlab.com"}


def _read_markdown_files(root: Path, matcher: IgnoreMatcher) -> list[SourceFile]:
    files: list[SourceFile] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git" and not matcher.is_ignored(d)]
        for name in filenames:
            full = Path(dirpath) / name
            relative = full.relative_to(root).as_posix()
            if matcher.is_ignored(relative) or not is_markdown(relative):
                continue
            try:
                data = full.read_bytes()
                stat = full.stat()
            except OSError:
                continue
            files.append(
                SourceFile(
                    path=relative,
                    data=data,
                    mtime=datetime.fromtimestamp(stat.st_mtime),
                )
            )
    return files


def _embedder():
    try:
        return build_provider(
            provider=settings.embedding_provider,
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
        )
    except EmbeddingUnavailable:
        return None


async def _load_job(session, job_id: UUID, workspace_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, workspace_id, kind, status, payload_json, parent_job_id "
            "FROM job_runs WHERE id = :id AND workspace_id = :ws"
        ),
        {"id": job_id, "ws": workspace_id},
    )
    row = result.first()
    return dict(row._mapping) if row else None


async def _set_job(session, job_id: UUID, workspace_id: UUID, **fields: Any) -> None:
    assignments = []
    params: dict[str, Any] = {"id": job_id, "ws": workspace_id}
    for key, value in fields.items():
        assignments.append(f"{key} = :{key}")
        if key.endswith("_json"):
            params[key] = json.dumps(value, ensure_ascii=False)
            assignments[-1] = f"{key} = CAST(:{key} AS jsonb)"
        else:
            params[key] = value
    if not assignments:
        return
    await session.execute(
        text(
            f"UPDATE job_runs SET {', '.join(assignments)} "
            f"WHERE id = :id AND workspace_id = :ws"
        ),
        params,
    )


async def _fail(job_id: UUID, workspace_id: UUID, reason: str, status: str = "failed") -> None:
    async with job_session(workspace_id) as session:
        await _set_job(
            session,
            job_id,
            workspace_id,
            status=status,
            error=reason[:2000],
            finished_at=datetime.now().astimezone(),
        )


async def run_job(job_id: UUID, workspace_id: UUID) -> None:
    """执行一个导入任务；异常一律落为 job 失败，不静默。"""
    try:
        async with job_session(workspace_id) as session:
            job = await _load_job(session, job_id, workspace_id)
            if job is None:
                logger.warning("job not found", extra={"job_id": str(job_id)})
                return
            if job["status"] != "queued":
                return

            payload = job["payload_json"] or {}
            if isinstance(payload, str):
                payload = json.loads(payload)

            await _set_job(
                session,
                job_id,
                workspace_id,
                status="running",
                started_at=datetime.now().astimezone(),
            )
            # 先提交“running”，让 API 轮询能看到状态；事务级 RLS 设置随后重建。
            await session.commit()
            await bind_workspace(session, workspace_id)

            storage_dir = (
                Path(settings.data_root)
                / "workspaces"
                / str(workspace_id)
                / "snapshots"
                / str(job_id)
            )

            commit_sha: str | None = None
            source_date = None
            # resync 任务按 payload 判定来源，而不是按 kind。
            is_zip = bool(payload.get("zip_path"))

            if is_zip:
                zip_path = Path(payload["zip_path"])
                extract_zip(zip_path, storage_dir)
                files = _read_markdown_files(storage_dir, IgnoreMatcher())
            else:
                connection = await _load_connection(session, payload.get("connection_id"))
                commit_sha, source_date, files, clone_dir = await _clone(
                    connection, workspace_id, job_id
                )
                storage_dir = clone_dir

            outcome = await ingest_files(
                session,
                workspace_id=workspace_id,
                files=files,
                commit_sha=commit_sha,
                storage_path=str(storage_dir),
                source_date=source_date,
                embedder=_embedder(),
                job_id=job_id,
            )

            status = "partial" if outcome.counters["failed"] else "succeeded"
            progress = {
                "total_files": len(files),
                "processed_files": len(files),
                "failed_files": outcome.counters["failed"],
                "skipped_files": 0,
            }
            await _set_job(
                session,
                job_id,
                workspace_id,
                status=status,
                counters_json=outcome.counters,
                failed_items_json=outcome.failures[:200],
                progress_json=progress,
                snapshot_id=outcome.snapshot_id,
                finished_at=datetime.now().astimezone(),
            )
            if not is_zip and payload.get("connection_id"):
                await session.execute(
                    text(
                        "UPDATE git_connections SET last_sync_at = now(), "
                        "last_sync_status = :status WHERE id = :id AND workspace_id = :ws"
                    ),
                    {
                        "status": "ok" if status == "succeeded" else "failed",
                        "id": payload["connection_id"],
                        "ws": workspace_id,
                    },
                )
    except (ZipRejected, GitRejected) as exc:
        await _fail(job_id, workspace_id, f"{exc.reason_code}: {exc.message}")
    except CredentialDecryptError:
        await _fail(job_id, workspace_id, "INGEST_AUTH_FAILED: 凭据不可解密")
    except Exception as exc:  # noqa: BLE001 - 任务边界必须记录所有失败
        logger.exception("ingest job failed", extra={"job_id": str(job_id)})
        await _fail(job_id, workspace_id, f"INGEST_FAILED: {type(exc).__name__}")


async def _load_connection(session, connection_id: Any) -> dict[str, Any]:
    if not connection_id:
        raise GitRejected("INGEST_AUTH_FAILED", "缺少 Git 连接")
    result = await session.execute(
        text(
            "SELECT id, provider, repo_full_name, base_branch, auth_type, "
            "credential_ciphertext FROM git_connections WHERE id = :id"
        ),
        {"id": connection_id},
    )
    row = result.first()
    if row is None:
        raise GitRejected("INGEST_AUTH_FAILED", "Git 连接不存在")
    return dict(row._mapping)


async def _clone(
    connection: dict[str, Any], workspace_id: UUID, job_id: UUID
) -> tuple[str, Any, list[SourceFile], Path]:
    provider = connection["provider"]
    host = GIT_HOSTS.get(provider)
    if host is None:
        raise GitRejected("GIT_PROVIDER_UNSUPPORTED", f"不支持的 provider: {provider}")

    repo = connection["repo_full_name"]
    if connection["auth_type"] == "deploy_key" and not settings.git_base_url:
        url = f"git@{host}:{repo}.git"
    else:
        base = (settings.git_base_url or f"https://{host}").rstrip("/")
        url = f"{base}/{repo}.git"

    credential: str | None = None
    if connection["credential_ciphertext"] is not None:
        credential = decrypt_credential(
            bytes(connection["credential_ciphertext"]),
            secret_key=settings.secret_key,
            workspace_id=str(workspace_id),
            provider="git",
        )

    # 每个 job 独立且短名目录：规避 Windows MAX_PATH（`.git/objects/...` 很长），
    # 也避免 .git 只读文件导致 rmtree 失败。
    destination = (
        Path(settings.data_root) / "workspaces" / str(workspace_id) / "snapshots" / str(job_id)
    )

    result = await asyncio.to_thread(
        clone_repository,
        url,
        connection["base_branch"],
        destination,
        auth_type=connection["auth_type"],
        credential=credential,
        allow_local=settings.allow_local_git,
    )
    files = _read_markdown_files(destination, IgnoreMatcher())
    return result.commit_sha, result.author_date, files, destination


def run_job_sync(job_id: str, workspace_id: str) -> None:
    asyncio.run(run_job(UUID(job_id), UUID(workspace_id)))
