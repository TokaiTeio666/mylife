"""Phase 1 数据访问：导入任务、Git 连接、文档详情、快照存储。"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api.ids import uuid7


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row._mapping) if row is not None else None


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# 导入任务
# ---------------------------------------------------------------------------


async def count_active_ingest(session: AsyncSession, workspace_id: UUID) -> int:
    result = await session.execute(
        text(
            "SELECT count(*) FROM job_runs WHERE workspace_id = :ws "
            "AND kind IN ('git','zip','sync','resync') AND status IN ('queued','running')"
        ),
        {"ws": workspace_id},
    )
    return int(result.scalar_one())


async def find_zip_job_by_sha(
    session: AsyncSession, workspace_id: UUID, zip_sha256: str
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, kind, status, progress_json, counters_json, failed_items_json, "
            "       snapshot_id, created_at, started_at, finished_at "
            "FROM job_runs WHERE workspace_id = :ws AND kind = 'zip' "
            "  AND payload_json ->> 'zip_sha256' = :sha "
            "  AND created_at > now() - interval '24 hours' "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"ws": workspace_id, "sha": zip_sha256},
    )
    return _row(result.first())


async def insert_job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    kind: str,
    payload: dict[str, Any],
    parent_job_id: UUID | None = None,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO job_runs (id, workspace_id, kind, status, payload_json, parent_job_id) "
            "VALUES (:id, :ws, :kind, 'queued', CAST(:payload AS jsonb), :parent) "
            "RETURNING id, workspace_id, kind, status, progress_json, counters_json, "
            "          failed_items_json, snapshot_id, created_at, started_at, finished_at"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "kind": kind,
            "payload": json.dumps(payload, ensure_ascii=False),
            "parent": parent_job_id,
        },
    )
    return dict(result.one()._mapping)


async def get_job(
    session: AsyncSession, workspace_id: UUID, job_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT j.id, j.workspace_id, j.kind, j.status, j.progress_json, j.counters_json, "
            "       j.failed_items_json, j.payload_json, j.parent_job_id, j.snapshot_id, "
            "       j.created_at, j.started_at, j.finished_at, j.error, "
            "       s.commit_sha, s.doc_count, s.as_of, s.manifest_digest "
            "FROM job_runs j LEFT JOIN source_snapshots s ON s.id = j.snapshot_id "
            "WHERE j.workspace_id = :ws AND j.id = :id"
        ),
        {"ws": workspace_id, "id": job_id},
    )
    return _row(result.first())


async def list_jobs(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    status_filter: str | None = None,
    kind: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    conditions = ["workspace_id = :ws"]
    params: dict[str, Any] = {"ws": workspace_id, "limit": limit}
    if status_filter:
        conditions.append("status = :status")
        params["status"] = status_filter
    if kind:
        conditions.append("kind = :kind")
        params["kind"] = kind
    result = await session.execute(
        text(
            "SELECT id, kind, status, progress_json, snapshot_id, created_at, "
            "       started_at, finished_at FROM job_runs "
            f"WHERE {' AND '.join(conditions)} ORDER BY created_at DESC, id DESC LIMIT :limit"
        ),
        params,
    )
    return _rows(result)


# ---------------------------------------------------------------------------
# Git 连接
# ---------------------------------------------------------------------------


async def insert_git_connection(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    provider: str,
    repo_full_name: str,
    base_branch: str,
    auth_type: str,
    credential_ciphertext: bytes | None,
    reviews_root: str,
    mode: str,
    sync_interval_minutes: int,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            INSERT INTO git_connections (
              id, workspace_id, role, provider, repo_full_name, base_branch, auth_type,
              credential_ciphertext, credential_key_version, reviews_root, mode,
              sync_enabled, sync_interval_minutes
            ) VALUES (
              :id, :ws, 'source', :provider, :repo, :branch, :auth_type,
              :ciphertext, :key_version, :reviews_root, :mode,
              :sync_enabled, :interval
            )
            RETURNING id, provider, repo_full_name, base_branch, mode, reviews_root,
                      sync_interval_minutes, last_sync_at, last_sync_status,
                      (credential_ciphertext IS NOT NULL) AS has_credential
            """
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "provider": provider,
            "repo": repo_full_name,
            "branch": base_branch,
            "auth_type": auth_type,
            "ciphertext": credential_ciphertext,
            "key_version": 1 if credential_ciphertext else None,
            "reviews_root": reviews_root,
            "mode": mode,
            "sync_enabled": sync_interval_minutes > 0,
            "interval": sync_interval_minutes,
        },
    )
    return dict(result.one()._mapping)


async def get_git_connection(
    session: AsyncSession, workspace_id: UUID, connection_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, provider, repo_full_name, base_branch, mode, reviews_root, "
            "       sync_interval_minutes, last_sync_at, last_sync_status, "
            "       (credential_ciphertext IS NOT NULL) AS has_credential "
            "FROM git_connections WHERE workspace_id = :ws AND id = :id"
        ),
        {"ws": workspace_id, "id": connection_id},
    )
    return _row(result.first())


async def list_git_connections(
    session: AsyncSession, workspace_id: UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, provider, repo_full_name, base_branch, mode, reviews_root, "
            "       sync_interval_minutes, last_sync_at, last_sync_status, "
            "       (credential_ciphertext IS NOT NULL) AS has_credential "
            "FROM git_connections WHERE workspace_id = :ws AND role = 'source' "
            "ORDER BY created_at ASC"
        ),
        {"ws": workspace_id},
    )
    return _rows(result)


async def list_zip_imports(
    session: AsyncSession, workspace_id: UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, status, created_at, finished_at FROM job_runs "
            "WHERE workspace_id = :ws AND kind = 'zip' ORDER BY created_at DESC LIMIT 20"
        ),
        {"ws": workspace_id},
    )
    return _rows(result)


# ---------------------------------------------------------------------------
# 文档详情 / 关联 / 快照存储
# ---------------------------------------------------------------------------


async def get_document(
    session: AsyncSession, workspace_id: UUID, document_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, path, title, content, frontmatter_json, content_hash, size, mtime, "
            "       indexed_at, tags, occurred_on, source_type "
            "FROM documents WHERE workspace_id = :ws AND id = :id"
        ),
        {"ws": workspace_id, "id": document_id},
    )
    return _row(result.first())


async def get_document_by_path(
    session: AsyncSession, workspace_id: UUID, path: str
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, path, title, content, frontmatter_json, content_hash, size, mtime, "
            "       indexed_at, tags, occurred_on, source_type "
            "FROM documents WHERE workspace_id = :ws AND path = :path"
        ),
        {"ws": workspace_id, "path": path},
    )
    return _row(result.first())


async def list_document_links(
    session: AsyncSession, workspace_id: UUID, document_id: UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT l.target_path, l.link_text, d.id AS target_document_id "
            "FROM document_links l "
            "LEFT JOIN documents d ON d.workspace_id = l.workspace_id "
            "  AND (d.path = l.target_path OR d.path = l.target_path || '.md') "
            "WHERE l.workspace_id = :ws AND l.source_document_id = :doc"
        ),
        {"ws": workspace_id, "doc": document_id},
    )
    return _rows(result)


async def list_backlinks(
    session: AsyncSession, workspace_id: UUID, path: str
) -> list[dict[str, Any]]:
    stem = path[:-3] if path.endswith(".md") else path
    result = await session.execute(
        text(
            "SELECT l.target_path, l.link_text, d.id AS target_document_id "
            "FROM document_links l JOIN documents d ON d.id = l.source_document_id "
            "WHERE l.workspace_id = :ws AND (l.target_path = :path OR l.target_path = :stem)"
        ),
        {"ws": workspace_id, "path": path, "stem": stem},
    )
    return _rows(result)


async def list_chunks(session: AsyncSession, document_id: UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, heading, line_start, line_end, token_count FROM document_chunks "
            "WHERE document_id = :doc ORDER BY chunk_ordinal"
        ),
        {"doc": document_id},
    )
    return _rows(result)


async def document_counts(session: AsyncSession, document_id: UUID) -> tuple[int, int]:
    chunks = await session.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id = :doc"),
        {"doc": document_id},
    )
    links = await session.execute(
        text("SELECT count(*) FROM document_links WHERE source_document_id = :doc"),
        {"doc": document_id},
    )
    return int(chunks.scalar_one()), int(links.scalar_one())


async def document_snapshot_storage(
    session: AsyncSession, workspace_id: UUID, document_id: UUID
) -> str | None:
    result = await session.execute(
        text(
            "SELECT s.storage_path FROM snapshot_documents sd "
            "JOIN source_snapshots s ON s.id = sd.snapshot_id "
            "WHERE sd.workspace_id = :ws AND sd.document_id = :doc "
            "  AND s.storage_path IS NOT NULL ORDER BY s.as_of DESC LIMIT 1"
        ),
        {"ws": workspace_id, "doc": document_id},
    )
    return result.scalar_one_or_none()


async def snapshot_for_document(
    session: AsyncSession, workspace_id: UUID, document_id: UUID
) -> UUID | None:
    result = await session.execute(
        text(
            "SELECT snapshot_id FROM snapshot_documents "
            "WHERE workspace_id = :ws AND document_id = :doc "
            "ORDER BY snapshot_id DESC LIMIT 1"
        ),
        {"ws": workspace_id, "doc": document_id},
    )
    return result.scalar_one_or_none()
