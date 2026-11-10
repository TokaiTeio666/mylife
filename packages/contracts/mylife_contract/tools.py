"""ToolGateway 契约（API-CONTRACTS §5）。

入参模型 `extra="forbid"` 且不声明 `workspace_id`；上下文由 `ToolContext` 注入。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from mylife_contract.common import ApiModel, UtcDateTime
from mylife_contract.search import SearchResult


class SearchFilters(ApiModel):
    path_prefix: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    extensions: list[str] | None = None
    source_type: Literal["both", "document", "review"] = "document"


class SearchNotesArgs(ApiModel):
    query: str = Field(min_length=1, max_length=500)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(default=20, ge=1, le=50)
    mode: Literal["hybrid", "lexical", "vector"] = "hybrid"


class ReadNoteArgs(ApiModel):
    document_id: UUID | None = None
    path: str | None = Field(default=None, max_length=1024)
    heading: str | None = Field(default=None, max_length=200)
    line_range: list[int] | None = None
    window: int | None = Field(default=None, ge=1, le=200)

    @model_validator(mode="after")
    def _one_of(self) -> ReadNoteArgs:
        if (self.document_id is None) == (self.path is None):
            raise ValueError("exactly one of document_id or path is required")
        return self


class ReadNoteResult(ApiModel):
    document_id: UUID
    path: str
    title: str
    heading: str | None
    line_range: list[int] | None
    content: str
    content_hash: str
    truncated: bool


class ListTasksArgs(ApiModel):
    status: list[Literal["open", "done", "carried", "dropped"]] | None = None
    from_date: date | None = Field(default=None, alias="from")
    to_date: date | None = Field(default=None, alias="to")
    section_keys: list[str] | None = None
    limit: int = Field(default=50, ge=1, le=200)
    include_text: bool = True


class TaskItem(ApiModel):
    id: UUID
    text: str
    status: str
    first_seen: date
    last_seen: date
    done_date: date | None
    source_path: str | None
    line_number: int | None
    occurrence_count: int = 0
    days_open: int | None = None


class ListTasksResult(ApiModel):
    items: list[TaskItem]
    counts: dict[str, int]
    ledger_recalc: dict[str, Any]


class GetReviewArgs(ApiModel):
    review_id: UUID | None = None
    kind: Literal["daily", "monthly", "yearly"] | None = None
    period_key: str | None = Field(default=None, max_length=20)
    version: int | None = Field(default=None, ge=1)
    include: list[Literal["summary", "sections", "entries"]] = Field(
        default_factory=lambda: ["summary"]
    )

    @model_validator(mode="after")
    def _target(self) -> GetReviewArgs:
        if self.review_id is None and not (self.kind and self.period_key):
            raise ValueError("review_id or (kind, period_key) is required")
        return self


class GetReviewResult(ApiModel):
    review_id: UUID
    kind: str
    period_key: str
    version: int
    title: str
    sections: list[dict[str, Any]] = Field(default_factory=list)
    entries: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class GetStatsArgs(ApiModel):
    metric: Literal[
        "completion_rate",
        "task_counts",
        "review_counts",
        "token_usage",
        "no_evidence_ratio",
    ]
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    group_by: Literal["day", "week", "month", "kind"] | None = None


class GetStatsResult(ApiModel):
    metric: str
    value: Any
    sample_size: int
    as_of: UtcDateTime
    snapshot_id: UUID | None
    payload_hash: str


class CreateReviewArgs(ApiModel):
    kind: Literal["daily", "monthly", "yearly"]
    period_key: str = Field(min_length=4, max_length=20)
    timezone: str | None = Field(default=None, max_length=64)
    confirmation_token: str | None = Field(default=None, max_length=4096)


class EstimatedTokens(ApiModel):
    input: int = Field(ge=0)
    output: int = Field(ge=0)


class CreateReviewPreview(ApiModel):
    kind: str
    period_key: str
    title: str
    path: str
    estimated_evidence_count: int
    estimated_tokens: EstimatedTokens


class CreateReviewConfirmArgs(ApiModel):
    confirmation_token: str = Field(min_length=1, max_length=4096)


class CreateReviewConfirmResult(ApiModel):
    confirmed: bool
    confirmation_token: str


class CreateReviewResult(ApiModel):
    preview: CreateReviewPreview
    confirmation_token: str
    expires_at: UtcDateTime
    blocked_reason: str | None


class SearchNotesResult(ApiModel):
    items: list[SearchResult]
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    took_ms: int = 0
