"""mylife 契约包。

单一事实源：枚举、错误码、请求/响应模型、哨兵文案与限额。
本包不得依赖任何 app 或运行时包，也不得包含业务逻辑与 IO（DIRECTORY-CONSTRAINTS §2.1）。
"""

from mylife_contract.constants import (
    CAPABILITIES_BY_ROLE,
    COOKIE_CSRF,
    COOKIE_SESSION,
    DEFAULT_LIMITS,
    HEADER_CSRF,
    HEADER_REQUEST_ID,
    REVISION_LIMIT,
    SENTINELS,
)
from mylife_contract.enums import (
    ChatRunState,
    DegradedReason,
    DeletionState,
    DeliveryStatus,
    EmbeddingStatus,
    EvidenceType,
    ItemType,
    JobKind,
    JobStatus,
    Plan,
    RunKind,
    RunState,
    SourceType,
    TopicStatus,
    TriggerType,
    UserStatus,
    WorkspaceRole,
)
from mylife_contract.errors import ERROR_SPECS, ErrorCode, ErrorSpec

__all__ = [
    "CAPABILITIES_BY_ROLE",
    "COOKIE_CSRF",
    "COOKIE_SESSION",
    "DEFAULT_LIMITS",
    "ERROR_SPECS",
    "HEADER_CSRF",
    "HEADER_REQUEST_ID",
    "REVISION_LIMIT",
    "SENTINELS",
    "ChatRunState",
    "DegradedReason",
    "DeletionState",
    "DeliveryStatus",
    "EmbeddingStatus",
    "ErrorCode",
    "ErrorSpec",
    "EvidenceType",
    "ItemType",
    "JobKind",
    "JobStatus",
    "Plan",
    "RunKind",
    "RunState",
    "SourceType",
    "TopicStatus",
    "TriggerType",
    "UserStatus",
    "WorkspaceRole",
]
