"""枚举定义。与 DATA-MODEL.md §1.1、API-CONTRACTS.md §2、ARCHITECTURE §4.6 逐项对应。

规则：API/JSON 一律小写；服务端严格校验，未知值返回 VALIDATION_ERROR。
"""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Plan(StrEnum):
    FREE = "free"
    PRO = "pro"


class RunKind(StrEnum):
    CHAT = "chat"
    DAILY = "daily"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class RunState(StrEnum):
    CREATED = "created"
    SNAPSHOTTING = "snapshotting"
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    REVISING = "revising"
    HUMAN_GATE = "human_gate"
    DELIVERING = "delivering"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    NEEDS_HUMAN = "needs_human"
    SECURITY_ABORT = "security_abort"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChatRunState(StrEnum):
    """chat 精简分支（ARCHITECTURE §4.6）。"""

    CREATED = "created"
    RETRIEVING = "retrieving"
    DRAFTING = "drafting"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceType(StrEnum):
    USER = "user"
    GENERATED = "generated"


class EvidenceType(StrEnum):
    DOCUMENT = "document"
    CHUNK = "chunk"
    REVIEW = "review"
    TASK = "task"
    STATS = "stats"


class ItemType(StrEnum):
    ERROR = "error"
    METHOD = "method"
    ACTION = "action"


class TopicStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    RESOLVED = "resolved"
    STALE = "stale"
    DROPPED = "dropped"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    BLOCKED = "blocked"
    COMMITTING = "committing"
    PR_OPEN = "pr_open"
    MERGED = "merged"
    CONFLICT = "conflict"
    FAILED = "failed"
    NOOP = "noop"


class DeletionState(StrEnum):
    REQUESTED = "requested"
    COOLING = "cooling"
    PURGED = "purged"
    CANCELLED = "cancelled"


class EmbeddingStatus(StrEnum):
    ACTIVE = "active"
    BUILDING = "building"
    RETIRED = "retired"


class TriggerType(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    API = "api"
    CHAT = "chat"


class JobKind(StrEnum):
    INGEST = "ingest"
    EMBED = "embed"
    REVIEW = "review"
    DELIVERY = "delivery"
    EXPORT = "export"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class DegradedReason(StrEnum):
    """API-CONTRACTS §4.5；客户端必须容忍新增值。"""

    MISSING = "missing"
    DISABLED = "disabled"
    PROFILE_MISMATCH = "profile_mismatch"
    IO_ERROR = "io_error"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    VECTOR_TIMEOUT = "vector_timeout"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    INGEST_PARTIAL_FAILED = "ingest_partial_failed"
    LLM_UNAVAILABLE = "llm_unavailable"
    GIT_UNAVAILABLE = "git_unavailable"
    EPOCH_MISMATCH = "epoch_mismatch"
    CURSOR_EXPIRED = "cursor_expired"
    BUDGET_WARN = "budget_warn"
    TOPIC_NEEDS_CONFIRM = "topic_needs_confirm"


class ExportStatus(StrEnum):
    """导出任务状态（API-CONTRACTS §3.11.2；PHASE5-SPEC D3）。

    DB 历史值 `succeeded` 在服务层映射为 `ready`，读取端只需认识本枚举。
    """

    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"


class ProviderCredentialStatus(StrEnum):
    """凭据状态（API-CONTRACTS §3.9.1）；DB 值见 PHASE5-SPEC D6。"""

    VALID = "valid"
    INVALID = "invalid"
    UNTESTED = "untested"
    MISSING = "missing"


class ReembedStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
