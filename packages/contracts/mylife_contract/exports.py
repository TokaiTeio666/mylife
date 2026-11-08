"""导出契约（API-CONTRACTS §3.11.1–§3.11.3；AC-55 / AC-64）。

导出是异步任务：`POST /exports` 返回 202 + `queued`，客户端轮询 `GET /exports/{id}`，
就绪后走 `GET /exports/{id}/download`。下载实现为直传 ZIP（PHASE5-SPEC D1），
预签名 302 属部署期可选增强。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from mylife_contract.common import ApiModel, UtcDateTime

ExportScope = Literal["workspace", "account"]
ExportStatus = Literal["queued", "running", "ready", "failed", "expired"]
ExportIncludeKey = Literal["documents", "reviews", "conversations", "settings", "usage", "audit"]

#: AC-64 要求覆盖的五类 + 审计（PHASE5-SPEC D29）。
EXPORT_INCLUDE_KEYS: tuple[str, ...] = (
    "documents",
    "reviews",
    "conversations",
    "settings",
    "usage",
    "audit",
)


class ExportCreate(ApiModel):
    scope: ExportScope = "workspace"
    include: list[ExportIncludeKey] = Field(
        default_factory=lambda: ["documents", "reviews", "conversations", "settings", "usage"]
    )
    format: Literal["zip"] = "zip"

    @field_validator("include")
    @classmethod
    def _no_duplicates(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("include must not contain duplicates")
        if not value:
            raise ValueError("include must not be empty")
        return value


class ExportCreated(ApiModel):
    export_id: UUID
    status: Literal["queued"] = "queued"
    expires_at: UtcDateTime


class ExportProgress(ApiModel):
    files: int = 0
    bytes: int = 0


class ExportError(ApiModel):
    code: str
    message: str


class ExportJob(ApiModel):
    id: UUID
    scope: ExportScope
    status: ExportStatus
    progress: ExportProgress = Field(default_factory=ExportProgress)
    artifact_bytes: int | None = None
    created_at: UtcDateTime
    expires_at: UtcDateTime | None = None
    error: ExportError | None = None
