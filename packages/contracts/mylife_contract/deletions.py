"""删除冷却契约（API-CONTRACTS §3.11.4–§3.11.7；SEC-27）。

Phase 4 只实现 SEC-27 所需子集：requested → cooling → purged / cancelled。
导出 ZIP 与完整的撤销演练属 Phase 5（PHASE4-SPEC D17/D20）。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from mylife_contract.common import ApiModel, UtcDateTime

DeletionScope = Literal["workspace", "account"]
DeletionState = Literal["requested", "cooling", "purged", "cancelled"]


class DeletionNotice(ApiModel):
    will_delete: list[str] = Field(default_factory=list)
    will_retain: list[str] = Field(default_factory=list)
    notice: str


class DeletionCreate(ApiModel):
    scope: DeletionScope = "workspace"
    confirm_text: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=500)


class DeletionRequest(ApiModel):
    deletion_id: UUID
    scope: DeletionScope
    state: DeletionState
    purge_at: UtcDateTime
    requested_at: UtcDateTime
    requested_by: UUID
    can_cancel: bool = False
    export_recommended: bool = False
    notice: DeletionNotice
