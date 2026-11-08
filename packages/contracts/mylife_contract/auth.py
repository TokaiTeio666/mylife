"""认证与账户契约（API-CONTRACTS.md §3.1）。

字段名、可空性、默认值与文档逐项一致；所有请求模型 `extra="forbid"` 且拒绝 `workspace_id`。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, field_validator

from mylife_contract.common import ApiModel, EmailMixin, UtcDateTime
from mylife_contract.constants import (
    DISPLAY_NAME_MAX_LENGTH,
    INVITE_CODE_MAX_LENGTH,
    PASSWORD_MAX_LENGTH,
)
from mylife_contract.enums import Plan, UserStatus, WorkspaceRole


class User(ApiModel):
    """用户公开模型（§3.1.1）。

    `is_platform_admin` 供前端决定是否展示 `/admin` 入口（AC-54）；它只是
    可见性提示，服务端仍独立校验（前端不作为安全边界）。
    """

    id: UUID
    email: str
    display_name: str
    status: UserStatus
    created_at: UtcDateTime
    is_platform_admin: bool = False


class Workspace(ApiModel):
    """Workspace 公开模型（§3.1.1）；`role` 为当前用户在该 workspace 的角色。"""

    id: UUID
    name: str
    slug: str = Field(pattern=r"^[a-z0-9-]{3,48}$")
    role: WorkspaceRole
    plan: Plan
    created_at: UtcDateTime


class SessionInfo(ApiModel):
    expires_at: UtcDateTime
    active_workspace_id: UUID
    csrf_token: str


class TokenBudget(ApiModel):
    input_tokens: int
    output_tokens: int


class Limits(ApiModel):
    """§3.1.5 Limits（字段与文档逐项一致，不额外扩展）。"""

    review_concurrency: int = 2
    daily_budget: TokenBudget
    monthly_budget: TokenBudget
    max_zip_bytes: int = 209_715_200
    max_search_top_k: int = 50


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------


class RegisterRequest(ApiModel, EmailMixin):
    email: str
    # 长度在服务层校验，以便返回稳定的 AUTH_WEAK_PASSWORD / AUTH_INVITE_REQUIRED 错误码。
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH + 1)
    display_name: str
    invite_code: str | None = Field(default=None, max_length=INVITE_CODE_MAX_LENGTH + 1)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= DISPLAY_NAME_MAX_LENGTH:
            raise ValueError("display_name must be 1-64 characters")
        return v


class RegisterResponse(ApiModel):
    user: User
    workspace: Workspace
    session: SessionInfo


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------


class LoginRequest(ApiModel, EmailMixin):
    email: str
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class LoginResponse(ApiModel):
    user: User
    workspaces: list[Workspace]
    active_workspace_id: UUID
    csrf_token: str


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


class MeResponse(ApiModel):
    user: User
    workspaces: list[Workspace]
    active_workspace_id: UUID
    capabilities: list[str]
    limits: Limits


class CsrfResponse(ApiModel):
    csrf_token: str
