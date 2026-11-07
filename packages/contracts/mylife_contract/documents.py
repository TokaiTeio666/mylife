"""文档契约（API-CONTRACTS.md §3.4）。Phase 0 先落地目录树。"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from mylife_contract.common import ApiModel, UtcDateTime
from mylife_contract.enums import SourceType


class TreeNode(ApiModel):
    type: Literal["dir", "file"]
    path: str
    parent_path: str | None
    name: str
    title: str | None = None
    document_id: UUID | None = None
    size: int | None = None
    mtime: UtcDateTime | None = None
    occurred_on: date | None = None
    tags: list[str] = Field(default_factory=list)
    child_count: int | None = None
    source_type: SourceType = SourceType.USER


class DocumentTree(ApiModel):
    root: str = ""
    nodes: list[TreeNode]
    total_documents: int
    truncated: bool = False


class DocumentLink(ApiModel):
    target_path: str
    link_text: str | None
    resolved: bool
    target_document_id: UUID | None


class ChunkSummary(ApiModel):
    chunk_id: UUID
    heading: str | None
    line_range: list[int] | None
    token_count: int


class DocumentDetail(ApiModel):
    id: UUID
    path: str
    title: str
    content: str | None
    content_html: str | None
    frontmatter: dict
    tags: list[str]
    links: list[DocumentLink]
    backlinks: list[DocumentLink]
    content_hash: str
    size: int
    mtime: UtcDateTime
    occurred_on: date | None
    indexed_at: UtcDateTime
    source_type: SourceType
    chunks: list[ChunkSummary] | None = None


class DocumentMeta(ApiModel):
    id: UUID
    path: str
    title: str
    tags: list[str]
    size: int
    content_hash: str
    occurred_on: date | None
    mtime: UtcDateTime
    indexed_at: UtcDateTime
    source_type: SourceType
    snapshot_id: UUID | None = None
    chunk_count: int
    link_count: int
