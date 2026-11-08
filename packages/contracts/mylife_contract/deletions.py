"""删除冷却契约（API-CONTRACTS §3.11.4–§3.11.7；SEC-27 / AC-55 / AC-64）。

状态机：requested → cooling → purged / cancelled。Phase 5 补齐 `cancellable_until`
与“先导出”前置（PHASE5-SPEC D15/D17）。
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
    #: 跳过“先导出”前置检查；owner 显式承担后果（PHASE5-SPEC D15）。
    skip_export: bool = False


class DeletionRequest(ApiModel):
    deletion_id: UUID
    scope: DeletionScope
    state: DeletionState
    purge_at: UtcDateTime
    #: 可撤销截止时间；当前等于 `purge_at`（PHASE5-SPEC D17）。
    cancellable_until: UtcDateTime
    requested_at: UtcDateTime
    requested_by: UUID
    can_cancel: bool = False
    export_recommended: bool = False
    notice: DeletionNotice
