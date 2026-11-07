"""专题契约（API-CONTRACTS §3.7.13；决策 4B）。

DB 侧 topic 值为英文 `errors/methods/actions`；API 侧使用中文
`错误清单/正确方法论/行动清单`，由服务层做映射。
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from mylife_contract.common import ApiModel

TopicSlug = Literal["errors", "methods", "actions"]
TopicStatusValue = Literal["pending", "open", "resolved", "stale"]


class TopicSummary(ApiModel):
    topic: str
    title: str
    path: str
    open_count: int = 0
    last_updated: date | None = None


class TopicList(ApiModel):
    items: list[TopicSummary]


class TopicOccurrence(ApiModel):
    source_date: date
    source_path: str
    line_range: list[int] | None = None
    quote: str
    evidence_id: str | None = None


class TopicItem(ApiModel):
    item_id: UUID
    canonical_key: str
    title: str
    status: TopicStatusValue
    first_seen: date
    last_seen: date
    occurrence_count: int = 0
    occurrences: list[TopicOccurrence] = Field(default_factory=list)


class TopicItemList(ApiModel):
    items: list[TopicItem]
    next_cursor: str | None = None
    has_more: bool = False


class ConfirmRequest(ApiModel):
    decision: Literal["accept", "drop"]
    edited_fields: dict | None = None


class ResolveRequest(ApiModel):
    note: str | None = Field(default=None, max_length=500)
