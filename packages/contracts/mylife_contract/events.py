"""SSE 事件契约（API-CONTRACTS §6）。

持久化事件写 `run_events`；`token`/`progress`/`heartbeat` 仅走 SSE，不落库。
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from mylife_contract.common import ApiModel, UtcDateTime


class RunStateEvent(ApiModel):
    from_state: str | None
    to_state: str
    attempt_id: UUID | None
    at: UtcDateTime


class ToolCallEvent(ApiModel):
    seq: int
    role: str = "worker"
    tool_name: str
    args_digest: str
    args_summary: str
    at: UtcDateTime


class ToolResultEvent(ApiModel):
    seq: int
    status: Literal["ok", "error", "timeout", "skipped"]
    latency_ms: int
    result_digest: str
    result_summary: str


class CitationEvent(ApiModel):
    index: int
    evidence_id: str
    document_id: UUID
    chunk_id: UUID
    path: str
    heading: str | None = None
    line_range: list[int] | None = None


class TokenEvent(ApiModel):
    delta: str
    message_id: UUID
    seq: int


class HeartbeatEvent(ApiModel):
    at: UtcDateTime
    state: str


class DegradedEvent(ApiModel):
    reason: str
    scope: str
    section_key: str | None = None
    detail: str | None = None


class DoneEvent(ApiModel):
    state: str
    message_id: UUID | None = None
    review_id: UUID | None = None
    usage: dict[str, Any] | None = None


class ErrorEvent(ApiModel):
    code: str
    message: str
    state: str
    retryable: bool = False


class CancelledEvent(ApiModel):
    by: str = "user"
    reason: str | None = None


SSE_EVENT_MODELS: dict[str, type[ApiModel]] = {
    "run.state": RunStateEvent,
    "tool.call": ToolCallEvent,
    "tool.result": ToolResultEvent,
    "chat.tool_call": ToolCallEvent,
    "chat.tool_result": ToolResultEvent,
    "citation": CitationEvent,
    "chat.citation": CitationEvent,
    "degraded": DegradedEvent,
    "done": DoneEvent,
    "chat.done": DoneEvent,
    "error": ErrorEvent,
    "chat.error": ErrorEvent,
    "cancelled": CancelledEvent,
    "chat.cancelled": CancelledEvent,
    "heartbeat": HeartbeatEvent,
    "token": TokenEvent,
}


class StreamEnvelope(ApiModel):
    """通用 SSE data 载荷（未知事件由客户端忽略）。"""

    event: str
    payload: dict[str, Any] = Field(default_factory=dict)
