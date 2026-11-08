"""设置契约（API-CONTRACTS §3.9.7–§3.9.11；AC-51/AC-52）。"""

from __future__ import annotations

from datetime import time
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from mylife_contract.common import ApiModel, UtcDateTime


class ScheduleEnabled(ApiModel):
    daily: bool = True
    weekly: bool = True
    yearly: bool = True


class ScheduleNextRun(ApiModel):
    daily: UtcDateTime | None = None
    weekly: UtcDateTime | None = None
    yearly: UtcDateTime | None = None


class ScheduleSettings(ApiModel):
    timezone: str
    daily_at: time
    weekly_at: time
    yearly_at: time
    catchup_days: int = Field(default=7, ge=0, le=30)
    enabled: ScheduleEnabled = Field(default_factory=ScheduleEnabled)
    next_run_at: ScheduleNextRun = Field(default_factory=ScheduleNextRun)


class ScheduleUpdate(ApiModel):
    timezone: str = Field(min_length=1, max_length=64)
    daily_at: time
    weekly_at: time
    yearly_at: time
    catchup_days: int = Field(default=7, ge=0, le=30)
    enabled: ScheduleEnabled = Field(default_factory=ScheduleEnabled)

    @field_validator("timezone")
    @classmethod
    def _timezone_must_exist(cls, value: str) -> str:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA name") from exc
        return value


class GitConnectionSummary(ApiModel):
    id: UUID
    role: Literal["source", "output"]
    provider: Literal["github", "gitlab", "generic"]
    repo_full_name: str
    base_branch: str
    # 响应统一使用 wire 值 `app`（与 PUT 请求一致，PHASE4-SPEC D21）。
    auth_type: Literal["app", "deploy_key", "pat"]
    mode: str | None = None
    reviews_root: str | None = None
    sync_interval_minutes: int = 0
    sync_enabled: bool = False
    next_sync_at: UtcDateTime | None = None
    last_sync_at: UtcDateTime | None = None
    last_sync_status: str | None = None
    credential_status: Literal["valid", "invalid", "untested"] = "untested"
    last_tested_at: UtcDateTime | None = None


class GitConnectionList(ApiModel):
    items: list[GitConnectionSummary] = Field(default_factory=list)


class GitSettingsUpdate(ApiModel):
    role: Literal["source", "output"] = "source"
    provider: Literal["github", "gitlab"] = "github"
    repo_full_name: str = Field(min_length=3, max_length=200)
    base_branch: str = Field(default="main", min_length=1, max_length=255)
    # wire 值为 `app`，服务层映射为 DB `github_app`（PHASE4-SPEC D21）。
    auth_type: Literal["app", "deploy_key", "pat"] = "pat"
    credential: str | None = Field(default=None, max_length=20_000)
    reviews_root: str | None = Field(default=None, max_length=200)
    mode: Literal["managed", "vault_writeback", "separate"] | None = None
    sync_interval_minutes: int = Field(default=0, ge=0, le=1440)
    auto_merge_daily: bool | None = None
    # 自托管/测试远端覆盖（仅受控环境使用；SSRF 校验与 git 导入一致）。
    remote_url: str | None = Field(default=None, max_length=2048)
    api_base_url: str | None = Field(default=None, max_length=2048)

    @field_validator("reviews_root")
    @classmethod
    def _no_dotdot(cls, value: str | None) -> str | None:
        if value is not None and ".." in value.split("/"):
            raise ValueError("reviews_root must not contain '..'")
        return value


class UsageTotals(ApiModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    review_runs: int = 0
    cost: float = 0.0


class UsageBucket(ApiModel):
    bucket: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0


class UsageBudget(ApiModel):
    input_tokens: int = 0
    output_tokens: int = 0
    ratio: float = 0.0
    state: Literal["ok", "warn", "exceeded"] = "ok"


class UsageResponse(ApiModel):
    totals: UsageTotals
    series: list[UsageBucket] = Field(default_factory=list)
    budget: dict[str, UsageBudget] = Field(default_factory=dict)


class RetrievalSettings(ApiModel):
    retrieval_top_k: int = Field(default=20, ge=1, le=50)
    context_window_tokens: int = Field(default=32_000, ge=8_000, le=128_000)
    web_search_enabled: bool = False
    vector_enabled: bool = True


class RetrievalUpdate(ApiModel):
    retrieval_top_k: int = Field(default=20, ge=1, le=50)
    context_window_tokens: int = Field(default=32_000, ge=8_000, le=128_000)
    web_search_enabled: bool = False
