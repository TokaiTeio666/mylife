"""Chat 契约（API-CONTRACTS §3.6；ARCHITECTURE §4.6）。"""

from __future__ import annotations

from datetime import date  # noqa: F401  (re-export for tool payloads)
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from mylife_contract.budget import Budget, BudgetUsage
from mylife_contract.common import ApiModel, UtcDateTime


class Reference(ApiModel):
    index: int = Field(ge=1)
    document_id: UUID
    chunk_id: UUID
    path: str
    title: str
    heading: str | None
    line_range: list[int] | None
    snippet: str


class ToolEvent(ApiModel):
    seq: int
    tool_name: str
    args_digest: str
    args_summary: str
    status: Literal["ok", "error", "timeout", "skipped"]
    latency_ms: int


class MessageUsage(ApiModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str
    latency_ms: int


class Message(ApiModel):
    id: UUID
    conversation_id: UUID
    role: Literal["user", "assistant", "system"]
    content: str
    status: Literal["drafting", "complete", "partial", "cancelled"]
    references: list[Reference] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    usage: MessageUsage | None = None
    run_id: UUID | None = None
    created_at: UtcDateTime


class MessageList(ApiModel):
    items: list[Message]
    next_cursor: str | None = None
    has_more: bool = False


class ConversationSummary(ApiModel):
    id: UUID
    title: str
    created_at: UtcDateTime
    updated_at: UtcDateTime
    message_count: int
    last_message_preview: str


class ConversationList(ApiModel):
    items: list[ConversationSummary]
    next_cursor: str | None = None
    has_more: bool = False


class CreateConversationRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)


class UpdateConversationRequest(ApiModel):
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class ChatOptions(ApiModel):
    retrieval_top_k: int | None = Field(default=None, ge=1, le=50)
    context_window_tokens: int | None = Field(default=None, ge=4000, le=200000)
    web_search_enabled: bool = False
    model_profile: str = "default"
    budget: Budget | None = None


class ChatTurnRequest(ApiModel):
    client_turn_id: UUID
    message: str = Field(min_length=1, max_length=4000)
    options: ChatOptions | None = None

    @field_validator("message")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class StreamInfo(ApiModel):
    events_url: str
    epoch: int
    after: int


class ChatTurnResponse(ApiModel):
    run_id: UUID
    message_id: UUID
    stream: StreamInfo


class CancelResponse(ApiModel):
    run_id: UUID
    state: Literal["cancelled"]


class RunStepView(ApiModel):
    seq: int
    role: str
    tool_name: str | None
    status: str
    model: str | None
    tokens: int = 0
    latency_ms: int | None


class RunDetail(ApiModel):
    run_id: UUID
    kind: Literal["chat", "daily", "weekly", "yearly"]
    state: str
    period_key: str | None
    conversation_id: UUID | None
    attempt_id: UUID | None
    retry_from_state: str | None
    snapshot_id: UUID | None
    version: int | None = None
    lease_expires_at: UtcDateTime | None = None
    budget: BudgetUsage = Field(default_factory=BudgetUsage)
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    steps: list[RunStepView] = Field(default_factory=list)
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    error: dict[str, Any] | None = None
