"""Workspace 契约（API-CONTRACTS.md §3.2）。

业务路径一律不带 workspace_id（§1.4）；workspace 由服务端会话注入。
"""

from __future__ import annotations

from datetime import time
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from mylife_contract.auth import Workspace
from mylife_contract.common import ApiModel, UtcDateTime


class WorkspaceListResponse(ApiModel):
    items: list[Workspace]


class WorkspaceCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 64:
            raise ValueError("name must be 1-64 characters")
        return v


class WorkspacePatchRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not 1 <= len(v) <= 64:
            raise ValueError("name must be 1-64 characters")
        return v


class SettingsSummary(ApiModel):
    """GET /workspaces/{id} 的 settings_summary（Phase 0 仅暴露已有字段）。"""

    timezone: str
    daily_at: time
    weekly_at: time
    yearly_at: time
    reviews_root: str


class WorkspaceCounts(ApiModel):
    documents: int
    reviews: int
    open_tasks: int


class WorkspaceDetail(ApiModel):
    workspace: Workspace
    settings_summary: SettingsSummary
    counts: WorkspaceCounts


class WorkspaceMember(ApiModel):
    user_id: UUID
    role: Literal["owner", "admin", "member"]
    joined_at: UtcDateTime


class WorkspaceMembersResponse(ApiModel):
    items: list[WorkspaceMember]


class WorkspaceSwitchResponse(ApiModel):
    active_workspace_id: UUID
    csrf_token: str


class WorkspaceScheduleDefaults(ApiModel):
    """创建 Workspace 时写入的默认调度（FR-E13）。"""

    daily_at: time = time(1, 0)
    weekly_at: time = time(1, 30)
    yearly_at: time = time(2, 0)
    timezone: str = "Asia/Shanghai"
