"""搜索契约（API-CONTRACTS §3.5 / ARCHITECTURE §5）。"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from mylife_contract.common import ApiModel


class SearchResult(ApiModel):
    source_type: Literal["document", "review"]
    document_id: UUID | None = None
    chunk_id: UUID | None = None
    review_id: UUID | None = None
    path: str
    title: str
    heading: str | None = None
    snippet: str
    snippet_html: str | None = None
    score: float
    matched_by: Literal["lexical", "vector", "both"]
    rank_lexical: int | None = None
    rank_vector: int | None = None
    line_range: list[int] | None = None
    occurred_on: date | None = None


class SearchResponse(ApiModel):
    items: list[SearchResult]
    total_estimated: int
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    took_ms: int


class SuggestItem(ApiModel):
    text: str
    path: str
    document_id: UUID


class SuggestResponse(ApiModel):
    items: list[SuggestItem]
