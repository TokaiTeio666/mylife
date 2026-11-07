"""文档详情、元数据、链接、资产代理（API-CONTRACTS §3.4）。"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
from uuid import UUID

from mylife_contract.documents import (
    ChunkSummary,
    DocumentDetail,
    DocumentLink,
    DocumentMeta,
)
from mylife_contract.errors import ErrorCode
from mylife_runtime.paths import PathRejected, safe_join
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repo_phase1 as repo
from mylife_api.errors import AppError
from mylife_api.rendering import render_markdown

ALLOWED_ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_ASSET_BYTES = 20 * 1024 * 1024


def _link_model(row: dict[str, Any]) -> DocumentLink:
    return DocumentLink(
        target_path=row["target_path"],
        link_text=row.get("link_text"),
        resolved=row.get("target_document_id") is not None,
        target_document_id=row.get("target_document_id"),
    )


async def get_detail(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    document_id: UUID,
    include: set[str],
) -> DocumentDetail:
    row = await repo.get_document(session, workspace_id, document_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND)

    content = row["content"] if "content" in include else None
    content_html = (
        render_markdown(row["content"], document_id=str(row["id"]))
        if "content" in include
        else None
    )
    links: list[DocumentLink] = []
    backlinks: list[DocumentLink] = []
    if "links" in include:
        links = [
            _link_model(r)
            for r in await repo.list_document_links(session, workspace_id, document_id)
        ]
        backlinks = [
            _link_model(r) for r in await repo.list_backlinks(session, workspace_id, row["path"])
        ]
    chunks = None
    if "chunks" in include:
        chunks = [
            ChunkSummary(
                chunk_id=c["id"],
                heading=c.get("heading"),
                line_range=(
                    [c["line_start"], c["line_end"]]
                    if c.get("line_start") is not None and c.get("line_end") is not None
                    else None
                ),
                token_count=c["token_count"],
            )
            for c in await repo.list_chunks(session, document_id)
        ]

    mtime = row.get("mtime") or row["indexed_at"]
    return DocumentDetail(
        id=row["id"],
        path=row["path"],
        title=row["title"],
        content=content,
        content_html=content_html,
        frontmatter=row.get("frontmatter_json") or {},
        tags=list(row.get("tags") or []),
        links=links,
        backlinks=backlinks,
        content_hash=row["content_hash"],
        size=row["size"],
        mtime=mtime,
        occurred_on=row.get("occurred_on"),
        indexed_at=row["indexed_at"],
        source_type=row["source_type"],
        chunks=chunks,
    )


async def get_meta(
    session: AsyncSession, *, workspace_id: UUID, document_id: UUID
) -> DocumentMeta:
    row = await repo.get_document(session, workspace_id, document_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND)
    chunk_count, link_count = await repo.document_counts(session, document_id)
    snapshot_id = await repo.snapshot_for_document(session, workspace_id, document_id)
    return DocumentMeta(
        id=row["id"],
        path=row["path"],
        title=row["title"],
        tags=list(row.get("tags") or []),
        size=row["size"],
        content_hash=row["content_hash"],
        occurred_on=row.get("occurred_on"),
        mtime=row.get("mtime") or row["indexed_at"],
        indexed_at=row["indexed_at"],
        source_type=row["source_type"],
        snapshot_id=snapshot_id,
        chunk_count=chunk_count,
        link_count=link_count,
    )


async def get_by_path(
    session: AsyncSession, *, workspace_id: UUID, path: str
) -> DocumentDetail:
    row = await repo.get_document_by_path(session, workspace_id, path)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return await get_detail(
        session,
        workspace_id=workspace_id,
        document_id=row["id"],
        include={"meta", "content"},
    )


def resolve_asset_path(storage_root: str, relative: str) -> Path:
    try:
        target = safe_join(Path(storage_root), relative, vault=True)
    except PathRejected as exc:
        raise AppError(ErrorCode.NOT_FOUND) from exc
    if target.suffix.lower() not in ALLOWED_ASSET_EXTENSIONS:
        raise AppError(ErrorCode.NOT_FOUND)
    if not target.is_file():
        raise AppError(ErrorCode.NOT_FOUND)
    if target.stat().st_size > MAX_ASSET_BYTES:
        raise AppError(ErrorCode.PAYLOAD_TOO_LARGE)
    return target


async def get_asset(
    session: AsyncSession, *, workspace_id: UUID, document_id: UUID, path: str
) -> tuple[Path, str]:
    document = await repo.get_document(session, workspace_id, document_id)
    if document is None:
        raise AppError(ErrorCode.NOT_FOUND)
    storage_root = await repo.document_snapshot_storage(session, workspace_id, document_id)
    if not storage_root:
        raise AppError(ErrorCode.NOT_FOUND)
    target = resolve_asset_path(storage_root, path)
    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return target, content_type
