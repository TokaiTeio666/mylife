"""通用模型：错误信封、分页、基础基类。

对应 API-CONTRACTS.md §1.5（错误信封）、§1.6（游标分页）、§1.4（workspace 注入不可协商）。
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Annotated, Any, ClassVar, Final, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

#: 出现即拒绝的字段名（API-CONTRACTS §1.4）。
FORBIDDEN_WORKSPACE_FIELD: Final = "workspace_id"
WORKSPACE_ID_NOT_ALLOWED: Final = "WORKSPACE_ID_NOT_ALLOWED"


class ApiModel(BaseModel):
    """所有请求/响应模型的基类。

    - `extra="forbid"`：未知字段直接 422（含 `workspace_id`）。
    - 响应中的可空字段必须显式输出 `null`，不得省略键（§1.2）。

    纯响应模型可能合法地携带 `workspace_id`（例如管理端的审计条目）。
    这类模型把 `allow_workspace_field` 置为 True；**请求模型不得这么做**
    （SEC-08，`test_contract.py` 覆盖）。
    """

    model_config = ConfigDict(extra="forbid")

    #: 仅供响应模型覆盖：允许出现 `workspace_id` 字段本身。
    allow_workspace_field: ClassVar[bool] = False

    @model_validator(mode="before")
    @classmethod
    def _reject_workspace_id(cls, data: Any) -> Any:
        """HTTP 入参一律不声明 workspace_id（§1.4，SEC-08）。

        对 dict 输入检查键；对非 dict 输入（如模型实例）跳过。
        """
        if cls.allow_workspace_field:
            return data
        if isinstance(data, dict) and FORBIDDEN_WORKSPACE_FIELD in data:
            raise ValueError(WORKSPACE_ID_NOT_ALLOWED)
        return data


def _serialize_utc(value: datetime) -> str:
    """RFC3339 UTC，统一输出 `2026-11-06T01:30:00Z`（API-CONTRACTS §1.2）。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


UtcDateTime = Annotated[
    datetime, PlainSerializer(_serialize_utc, return_type=str, when_used="json")
]
"""响应时间戳统一用此类型序列化为 `...Z`。"""


class ErrorDetail(BaseModel):
    """字段级错误明细。不得回显凭据（§1.5）。"""

    model_config = ConfigDict(extra="allow")

    field: str | None = None
    reason: str | None = None


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="稳定机器码，见 API-CONTRACTS §2")
    message: str = Field(description="简体中文面向用户文案")
    details: Any | None = Field(default=None, description="object / array / null")
    retryable: bool = Field(description="与 §2 表一致，客户端不得自行推断")


class ErrorEnvelope(BaseModel):
    """统一错误信封（§1.5）。"""

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
    request_id: str


class Page[T](BaseModel):
    """游标分页响应（§1.6）。"""

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    next_cursor: str | None = Field(default=None)
    has_more: bool


class PageQuery(BaseModel):
    """分页请求参数。"""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None


class Timestamps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at: datetime
    updated_at: datetime | None = None


def normalize_email(value: str) -> str:
    """邮箱归一化：去首尾空白 + 小写（§3.1.1）。"""
    return value.strip().lower()


class EmailMixin(BaseModel):
    """邮箱字段校验（≤254、含 local@domain.tld、小写归一）。"""

    model_config = ConfigDict(extra="forbid")

    @field_validator("email", check_fields=False)
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = normalize_email(v)
        if len(v) > 254 or "@" not in v:
            raise ValueError("must be a valid email address")
        local, _, domain = v.partition("@")
        if not local or not domain or "." not in domain:
            raise ValueError("must be a valid email address")
        return v


def is_valid_uuid(value: str) -> bool:
    """UUIDv7 小写字符串（36 字符，带连字符）。"""
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


_DAILY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTHLY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_YEARLY_RE = re.compile(r"^\d{4}$")


def is_valid_period_key(kind: str, value: str) -> bool:
    """按 kind 校验 period_key 格式（§1.2）。"""
    if kind == "daily":
        if not _DAILY_RE.match(value):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if kind == "monthly":
        return bool(_MONTHLY_RE.match(value))
    if kind == "yearly":
        if not _YEARLY_RE.match(value):
            return False
        return 1970 <= int(value) <= 2999
    return False


BusinessDate = date
"""业务日期：YYYY-MM-DD，不带时区（§1.2）。"""

JsonObject = dict[str, Any]
"""无 schema 的 JSON 只允许短期使用；长期字段必须在 contracts 冻结结构。"""

SortDirection = Literal["asc", "desc"]
