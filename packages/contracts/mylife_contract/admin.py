"""管理后台契约（API-CONTRACTS.md §3.10；AC-54 / SEC-30）。

约束：所有 admin 端点双重校验（用户 `platform_admin` + 会话有效），每次操作写 `audit_logs`；
管理员不进入用户 Workspace 业务数据面——**本模块没有任何承载正文的模型**（SEC-30）。
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal
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


# ---------------------------------------------------------------------------
# Phase 5：用户 / Workspace / 审计 / 系统配置 / 用量（AC-54）
# ---------------------------------------------------------------------------


class AdminUserDisableRequest(ApiModel):
    reason: str = Field(min_length=1, max_length=500)


class AdminWorkspaceListResponse(ApiModel):
    items: list[AdminWorkspace]
    next_cursor: str | None = None


class AdminAuditLog(ApiModel):
    """审计条目（只含元数据；`detail` 已脱敏，不含正文与密钥）。"""

    # 响应模型，允许携带 workspace_id（请求模型禁止，SEC-08）。
    allow_workspace_field: ClassVar[bool] = True

    id: UUID
    workspace_id: UUID | None = None
    user_id: UUID | None = None
    action: str
    target: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    ip: str | None = None
    created_at: UtcDateTime


class AdminAuditLogList(ApiModel):
    items: list[AdminAuditLog]
    next_cursor: str | None = None


class AdminSystemConfigItem(ApiModel):
    key: str
    value: Any = None
    #: 由环境变量锁定，`PUT` 会被拒绝（ADMIN_SYSTEM_CONFIG_LOCKED）。
    locked: bool = False
    source: Literal["env", "db", "default"] = "default"


class AdminSystemConfigList(ApiModel):
    items: list[AdminSystemConfigItem]


class AdminSystemConfigUpdate(ApiModel):
    key: str = Field(min_length=1, max_length=100)
    value: Any = None


class AdminUsageWorkspace(ApiModel):
    allow_workspace_field: ClassVar[bool] = True

    workspace_id: UUID
    workspace_name: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    cost: float = 0.0


class AdminUsageResponse(ApiModel):
    totals: AdminUsageWorkspace | None = None
    items: list[AdminUsageWorkspace] = Field(default_factory=list)
