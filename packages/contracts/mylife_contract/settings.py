"""设置契约（API-CONTRACTS §3.9.1–§3.9.11；AC-49/AC-50/AC-51/AC-52/AC-53）。"""

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


# ---------------------------------------------------------------------------
# LLM（API-CONTRACTS §3.9.1–§3.9.3；AC-49）
# ---------------------------------------------------------------------------

LlmProviderName = Literal["deepseek", "openai_compatible", "mock"]
CredentialStatus = Literal["valid", "invalid", "untested", "missing"]


class LlmSettings(ApiModel):
    """明文 API Key 永不出现在响应里。"""

    provider: LlmProviderName | None = None
    base_url: str | None = None
    model: str | None = None
    model_profile: str = "default"
    credential_status: CredentialStatus = "missing"
    last_tested_at: UtcDateTime | None = None
    supports_json_mode: bool = False


class LlmUpdate(ApiModel):
    provider: LlmProviderName
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    # 省略 = 保持现有密钥（API §3.9.2）。
    api_key: str | None = Field(default=None, max_length=4096)
    test_before_save: bool = True


class LlmTestRequest(ApiModel):
    provider: LlmProviderName
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)


class ProviderTestError(ApiModel):
    code: str
    message: str


class LlmTestResult(ApiModel):
    ok: bool
    latency_ms: int = 0
    model_echo: str | None = None
    error: ProviderTestError | None = None


# ---------------------------------------------------------------------------
# Embedding（API-CONTRACTS §3.9.4–§3.9.6；AC-50）
# ---------------------------------------------------------------------------

EmbeddingStatus = Literal["active", "building", "retired"]


class EmbeddingProfile(ApiModel):
    id: UUID
    provider: str
    model: str
    dimension: int
    index_name: str
    status: EmbeddingStatus
    created_at: UtcDateTime
    # 已 embedding 的 chunk 占比；building 阶段用于校验（API §3.9.4）。
    coverage: float = 0.0
    chunk_table: str | None = None
    activated_at: UtcDateTime | None = None
    retire_at: UtcDateTime | None = None


class ReembedProgress(ApiModel):
    job_id: UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    processed_chunks: int = 0
    total_chunks: int = 0
    error: str | None = None


class EmbeddingSettings(ApiModel):
    active_profile: EmbeddingProfile | None = None
    profiles: list[EmbeddingProfile] = Field(default_factory=list)
    credential_status: CredentialStatus = "missing"
    last_tested_at: UtcDateTime | None = None
    # 后台 re-embed 任务的进度（PHASE5-SPEC §4.3）。
    reembed: ReembedProgress | None = None


class EmbeddingProfileCreate(ApiModel):
    provider: Literal["openai", "mock"]
    model: str = Field(min_length=1, max_length=200)
    dimension: int = Field(ge=256, le=3072)
    api_key: str | None = Field(default=None, max_length=4096)
    confirm: bool = False


class EmbeddingProfileCreated(ApiModel):
    profile: EmbeddingProfile
    job_id: UUID


class EmbeddingTestRequest(ApiModel):
    provider: Literal["openai", "mock"]
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)


class EmbeddingTestResult(ApiModel):
    ok: bool
    dimension_observed: int | None = None
    latency_ms: int = 0
    error: ProviderTestError | None = None
