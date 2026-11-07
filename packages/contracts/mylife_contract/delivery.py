"""交付契约（API-CONTRACTS §3.7.10–§4.8；ARCHITECTURE §8）。

Phase 3 只冻结契约与建表，交付实装属 Phase 4。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from mylife_contract.common import ApiModel, UtcDateTime


class DeliveryTarget(ApiModel):
    kind: Literal["git", "object_store", "ui_diff"]
    path: str
    content_hash: str
    mode: Literal["create_only", "update_allowed", "append_only"]


class PullRequestSpec(ApiModel):
    title: str
    body_md: str
    base_branch: str
    head_branch: str
    draft: bool = False
    auto_merge: bool = False


class ReviewRowRef(ApiModel):
    review_id: UUID
    version: int
    content_hash: str
    git_path: str


class DeliveryManifest(ApiModel):
    branch: str
    targets: list[DeliveryTarget] = Field(min_length=1)
    commit_message: str
    pr: PullRequestSpec | None = None
    review_row: ReviewRowRef


class DeliveryRecord(ApiModel):
    id: UUID
    review_id: UUID
    version: int
    target: Literal["git", "object_store", "ui_diff"]
    provider: Literal["github", "gitlab"] | None = None
    branch: str | None = None
    commit_sha: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    status: Literal[
        "pending",
        "committed",
        "pr_open",
        "merged",
        "closed",
        "noop",
        "blocked",
        "failed",
    ]
    merged_commit: str | None = None
    degraded: bool = False
    created_at: UtcDateTime
    updated_at: UtcDateTime


class DeliveryList(ApiModel):
    items: list[DeliveryRecord]
    next_cursor: str | None = None
    has_more: bool = False
