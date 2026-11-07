"""管理后台契约（API-CONTRACTS.md §3.10）。Phase 0 仅先落地邀请码创建/查询。

约束：所有 admin 端点双重校验（用户 `platform_admin` + 会话有效），每次操作写 `audit_logs`；
管理员不进入用户 Workspace 业务数据面（SEC-30）。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from mylife_contract.common import ApiModel, UtcDateTime
from mylife_contract.enums import UserStatus


class InviteCodeCreateRequest(ApiModel):
    count: int = Field(ge=1, le=50, default=1)
    expires_in_days: int = Field(ge=1, le=365, default=30)
    note: str | None = Field(default=None, max_length=200)


class InviteCodeCreated(ApiModel):
    code: str
    expires_at: UtcDateTime


class InviteCodeCreateResponse(ApiModel):
    items: list[InviteCodeCreated]


class InviteCode(ApiModel):
    id: UUID
    code: str
    created_by: UUID
    used_by: UUID | None
    expires_at: UtcDateTime | None
    used_at: UtcDateTime | None
    note: str | None = None
    status: str


class InviteCodeListResponse(ApiModel):
    items: list[InviteCode]
    next_cursor: str | None = None


class AdminUser(ApiModel):
    id: UUID
    email: str
    display_name: str
    status: UserStatus
    created_at: UtcDateTime
    workspace_count: int = 0
    last_login_at: UtcDateTime | None = None


class AdminUserListResponse(ApiModel):
    items: list[AdminUser]
    next_cursor: str | None = None


class AdminWorkspaceCounts(ApiModel):
    documents: int = 0
    reviews: int = 0
    tokens_month: int = 0


class AdminWorkspace(ApiModel):
    id: UUID
    name: str
    slug: str
    owner_id: UUID
    plan: str
    created_at: UtcDateTime
    counts: AdminWorkspaceCounts
    deletion_state: str | None = None
