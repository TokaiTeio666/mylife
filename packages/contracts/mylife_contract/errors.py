"""错误码表（API-CONTRACTS.md §2）。单一事实源。

规则：
- 错误码一经发布不得改语义，新增只能追加（§2.1）。
- HTTP 状态与 retryable 由本表决定，客户端不得自行推断（§1.5）。
- message 为简体中文面向用户文案，不得含堆栈、SQL、token。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    # --- 通用（§2.2）---
    VALIDATION_ERROR = "VALIDATION_ERROR"
    BAD_REQUEST = "BAD_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    WORKSPACE_MISMATCH = "WORKSPACE_MISMATCH"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    CONFLICT = "CONFLICT"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    DEGRADED = "DEGRADED"
    SECURITY_ABORT = "SECURITY_ABORT"
    TENANT_LEAK = "TENANT_LEAK"
    FABRICATION = "FABRICATION"
    PROMPT_INJECTION_BLOCKED = "PROMPT_INJECTION_BLOCKED"
    SCHEMA_MIGRATION_FAILED = "SCHEMA_MIGRATION_FAILED"
    EVENT_CURSOR_EXPIRED = "EVENT_CURSOR_EXPIRED"
    CURSOR_INVALID = "CURSOR_INVALID"

    # --- 认证与账户（§2.3）---
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_SESSION_EXPIRED = "AUTH_SESSION_EXPIRED"
    AUTH_SESSION_REVOKED = "AUTH_SESSION_REVOKED"
    AUTH_CSRF_INVALID = "AUTH_CSRF_INVALID"
    AUTH_ACCOUNT_DISABLED = "AUTH_ACCOUNT_DISABLED"
    AUTH_ACCOUNT_LOCKED = "AUTH_ACCOUNT_LOCKED"
    AUTH_EMAIL_EXISTS = "AUTH_EMAIL_EXISTS"
    AUTH_WEAK_PASSWORD = "AUTH_WEAK_PASSWORD"
    AUTH_INVITE_REQUIRED = "AUTH_INVITE_REQUIRED"
    AUTH_INVITE_INVALID = "AUTH_INVITE_INVALID"
    AUTH_INVITE_EXPIRED = "AUTH_INVITE_EXPIRED"
    AUTH_INVITE_USED = "AUTH_INVITE_USED"
    AUTH_RATE_LIMITED = "AUTH_RATE_LIMITED"
    AUTH_EMAIL_NOT_VERIFIED = "AUTH_EMAIL_NOT_VERIFIED"

    # --- 导入（§2.4）---
    INGEST_REPO_UNREACHABLE = "INGEST_REPO_UNREACHABLE"
    INGEST_AUTH_FAILED = "INGEST_AUTH_FAILED"
    INGEST_BRANCH_NOT_FOUND = "INGEST_BRANCH_NOT_FOUND"
    INGEST_IGNORE_ALL = "INGEST_IGNORE_ALL"
    INGEST_ZIP_INVALID = "INGEST_ZIP_INVALID"
    INGEST_ZIP_LIMIT = "INGEST_ZIP_LIMIT"
    INGEST_JOB_NOT_FOUND = "INGEST_JOB_NOT_FOUND"
    INGEST_JOB_NOT_RETRYABLE = "INGEST_JOB_NOT_RETRYABLE"
    INGEST_PARTIAL = "INGEST_PARTIAL"
    INGEST_SNAPSHOT_STALE = "INGEST_SNAPSHOT_STALE"
    INGEST_CONCURRENCY_LIMIT = "INGEST_CONCURRENCY_LIMIT"

    # --- 复盘与交付（§2.5）---
    ACTIVE_RUN_EXISTS = "ACTIVE_RUN_EXISTS"
    REVIEW_PERIOD_INVALID = "REVIEW_PERIOD_INVALID"
    REVIEW_PERIOD_TOO_OLD = "REVIEW_PERIOD_TOO_OLD"
    REVIEW_NO_SOURCE_DATA = "REVIEW_NO_SOURCE_DATA"
    REVIEW_GATE_FAILED = "REVIEW_GATE_FAILED"
    REVIEW_REVISION_LIMIT = "REVIEW_REVISION_LIMIT"
    REVIEW_HUMAN_REQUIRED = "REVIEW_HUMAN_REQUIRED"
    REVIEW_ALREADY_DECIDED = "REVIEW_ALREADY_DECIDED"
    REVIEW_TOPIC_NEEDS_CONFIRM = "REVIEW_TOPIC_NEEDS_CONFIRM"
    REVIEW_VERSION_NOT_FOUND = "REVIEW_VERSION_NOT_FOUND"
    DELIVERY_GIT_UNAVAILABLE = "DELIVERY_GIT_UNAVAILABLE"
    DELIVERY_CREDENTIAL_INVALID = "DELIVERY_CREDENTIAL_INVALID"
    DELIVERY_PR_CONFLICT = "DELIVERY_PR_CONFLICT"
    DELIVERY_NOOP = "DELIVERY_NOOP"
    DELIVERY_NOT_FOUND = "DELIVERY_NOT_FOUND"
    GIT_MARKER_BROKEN = "GIT_MARKER_BROKEN"
    GIT_FORCE_PUSH_DENIED = "GIT_FORCE_PUSH_DENIED"
    GIT_PROVIDER_UNSUPPORTED = "GIT_PROVIDER_UNSUPPORTED"
    WEBHOOK_SIGNATURE_INVALID = "WEBHOOK_SIGNATURE_INVALID"
    WEBHOOK_REPLAY = "WEBHOOK_REPLAY"

    # --- 工具、搜索与模型依赖（§2.6）---
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_ARGS_INVALID = "TOOL_ARGS_INVALID"
    TOOL_DRIFT = "TOOL_DRIFT"
    SEARCH_VECTOR_TIMEOUT = "SEARCH_VECTOR_TIMEOUT"
    SEARCH_EMPTY_QUERY = "SEARCH_EMPTY_QUERY"
    SEARCH_TOO_MANY_TERMS = "SEARCH_TOO_MANY_TERMS"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    EMBEDDING_UNAVAILABLE = "EMBEDDING_UNAVAILABLE"
    EMBEDDING_PROFILE_MISMATCH = "EMBEDDING_PROFILE_MISMATCH"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    PROVIDER_KEY_MISSING = "PROVIDER_KEY_MISSING"

    # --- 管理、导出与删除（§2.7）---
    ADMIN_REQUIRED = "ADMIN_REQUIRED"
    ADMIN_USER_NOT_FOUND = "ADMIN_USER_NOT_FOUND"
    ADMIN_USER_DISABLED = "ADMIN_USER_DISABLED"
    ADMIN_INVITE_LIMIT = "ADMIN_INVITE_LIMIT"
    ADMIN_SYSTEM_CONFIG_LOCKED = "ADMIN_SYSTEM_CONFIG_LOCKED"
    EXPORT_NOT_READY = "EXPORT_NOT_READY"
    EXPORT_EXPIRED = "EXPORT_EXPIRED"
    EXPORT_LIMIT = "EXPORT_LIMIT"
    DELETE_ALREADY_REQUESTED = "DELETE_ALREADY_REQUESTED"
    DELETE_IN_COOLING = "DELETE_IN_COOLING"
    DELETE_NOT_CANCELLABLE = "DELETE_NOT_CANCELLABLE"


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    code: ErrorCode
    http_status: int
    retryable: bool
    message: str


#: 每个错误码的 HTTP 状态、可重试性与用户文案（API-CONTRACTS §2.2–§2.7 逐行对应）。
ERROR_SPECS: dict[ErrorCode, ErrorSpec] = {
    # §2.2 通用
    ErrorCode.VALIDATION_ERROR: ErrorSpec(ErrorCode.VALIDATION_ERROR, 422, False, "请求参数不合法"),
    ErrorCode.BAD_REQUEST: ErrorSpec(ErrorCode.BAD_REQUEST, 400, False, "请求格式不正确"),
    ErrorCode.NOT_FOUND: ErrorSpec(ErrorCode.NOT_FOUND, 404, False, "资源不存在"),
    ErrorCode.FORBIDDEN: ErrorSpec(ErrorCode.FORBIDDEN, 403, False, "没有权限执行该操作"),
    ErrorCode.WORKSPACE_MISMATCH: ErrorSpec(ErrorCode.WORKSPACE_MISMATCH, 404, False, "资源不存在"),
    ErrorCode.METHOD_NOT_ALLOWED: ErrorSpec(ErrorCode.METHOD_NOT_ALLOWED, 405, False, "不支持该请求方法"),
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: ErrorSpec(ErrorCode.UNSUPPORTED_MEDIA_TYPE, 415, False, "不支持的媒体类型"),
    ErrorCode.PAYLOAD_TOO_LARGE: ErrorSpec(ErrorCode.PAYLOAD_TOO_LARGE, 413, False, "内容超过大小上限"),
    ErrorCode.CONFLICT: ErrorSpec(ErrorCode.CONFLICT, 409, False, "当前状态与操作冲突"),
    ErrorCode.PRECONDITION_FAILED: ErrorSpec(ErrorCode.PRECONDITION_FAILED, 412, False, "前置条件不满足"),
    ErrorCode.IDEMPOTENCY_KEY_CONFLICT: ErrorSpec(ErrorCode.IDEMPOTENCY_KEY_CONFLICT, 409, False, "同一幂等键对应了不同的请求内容"),
    ErrorCode.RATE_LIMITED: ErrorSpec(ErrorCode.RATE_LIMITED, 429, True, "请求过于频繁，请稍后再试"),
    ErrorCode.QUOTA_EXCEEDED: ErrorSpec(ErrorCode.QUOTA_EXCEEDED, 429, False, "已超过配额上限"),
    ErrorCode.BUDGET_EXCEEDED: ErrorSpec(ErrorCode.BUDGET_EXCEEDED, 429, False, "已超过 token 预算"),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(ErrorCode.INTERNAL_ERROR, 500, True, "服务内部错误"),
    ErrorCode.SERVICE_UNAVAILABLE: ErrorSpec(ErrorCode.SERVICE_UNAVAILABLE, 503, True, "服务暂时不可用"),
    ErrorCode.NOT_IMPLEMENTED: ErrorSpec(ErrorCode.NOT_IMPLEMENTED, 501, False, "该功能尚未实现"),
    ErrorCode.DEGRADED: ErrorSpec(ErrorCode.DEGRADED, 200, False, "部分能力降级"),
    ErrorCode.SECURITY_ABORT: ErrorSpec(ErrorCode.SECURITY_ABORT, 403, False, "安全策略终止了本次操作"),
    ErrorCode.TENANT_LEAK: ErrorSpec(ErrorCode.TENANT_LEAK, 403, False, "安全策略终止了本次操作"),
    ErrorCode.FABRICATION: ErrorSpec(ErrorCode.FABRICATION, 422, False, "内容缺少可验证的依据"),
    ErrorCode.PROMPT_INJECTION_BLOCKED: ErrorSpec(ErrorCode.PROMPT_INJECTION_BLOCKED, 422, False, "该文档包含被隔离的指令性内容"),
    ErrorCode.SCHEMA_MIGRATION_FAILED: ErrorSpec(ErrorCode.SCHEMA_MIGRATION_FAILED, 422, False, "数据结构版本无法迁移"),
    ErrorCode.EVENT_CURSOR_EXPIRED: ErrorSpec(ErrorCode.EVENT_CURSOR_EXPIRED, 410, False, "事件游标已超出保留窗口"),
    ErrorCode.CURSOR_INVALID: ErrorSpec(ErrorCode.CURSOR_INVALID, 400, False, "分页游标无效"),
    # §2.3 认证
    ErrorCode.AUTH_INVALID_CREDENTIALS: ErrorSpec(ErrorCode.AUTH_INVALID_CREDENTIALS, 401, False, "邮箱或密码不正确"),
    ErrorCode.AUTH_SESSION_EXPIRED: ErrorSpec(ErrorCode.AUTH_SESSION_EXPIRED, 401, True, "登录状态已过期"),
    ErrorCode.AUTH_SESSION_REVOKED: ErrorSpec(ErrorCode.AUTH_SESSION_REVOKED, 401, False, "登录状态已失效"),
    ErrorCode.AUTH_CSRF_INVALID: ErrorSpec(ErrorCode.AUTH_CSRF_INVALID, 403, True, "请求校验失败，请重试"),
    # AC-54 明确要求“禁用账号后该用户登录 401”；API §2.3 表格写 403，
    # 以验收条款为准（PHASE5-SPEC D33）。已登录会话被禁用后同样返回 401。
    ErrorCode.AUTH_ACCOUNT_DISABLED: ErrorSpec(ErrorCode.AUTH_ACCOUNT_DISABLED, 401, False, "账号已被禁用，请联系管理员"),
    ErrorCode.AUTH_ACCOUNT_LOCKED: ErrorSpec(ErrorCode.AUTH_ACCOUNT_LOCKED, 429, True, "尝试次数过多，请稍后再试"),
    ErrorCode.AUTH_EMAIL_EXISTS: ErrorSpec(ErrorCode.AUTH_EMAIL_EXISTS, 409, False, "该邮箱已注册"),
    ErrorCode.AUTH_WEAK_PASSWORD: ErrorSpec(ErrorCode.AUTH_WEAK_PASSWORD, 422, False, "密码需为 8 到 128 个字符"),
    ErrorCode.AUTH_INVITE_REQUIRED: ErrorSpec(ErrorCode.AUTH_INVITE_REQUIRED, 403, False, "注册需要邀请码"),
    ErrorCode.AUTH_INVITE_INVALID: ErrorSpec(ErrorCode.AUTH_INVITE_INVALID, 400, False, "邀请码无效"),
    ErrorCode.AUTH_INVITE_EXPIRED: ErrorSpec(ErrorCode.AUTH_INVITE_EXPIRED, 400, False, "邀请码已过期"),
    ErrorCode.AUTH_INVITE_USED: ErrorSpec(ErrorCode.AUTH_INVITE_USED, 400, False, "该邀请码已被使用"),
    ErrorCode.AUTH_RATE_LIMITED: ErrorSpec(ErrorCode.AUTH_RATE_LIMITED, 429, True, "操作过于频繁，请稍后再试"),
    ErrorCode.AUTH_EMAIL_NOT_VERIFIED: ErrorSpec(ErrorCode.AUTH_EMAIL_NOT_VERIFIED, 403, False, "邮箱尚未验证"),
    # §2.4 导入
    ErrorCode.INGEST_REPO_UNREACHABLE: ErrorSpec(ErrorCode.INGEST_REPO_UNREACHABLE, 502, True, "无法访问 Git 仓库"),
    ErrorCode.INGEST_AUTH_FAILED: ErrorSpec(ErrorCode.INGEST_AUTH_FAILED, 422, False, "Git 凭据无效或权限不足"),
    ErrorCode.INGEST_BRANCH_NOT_FOUND: ErrorSpec(ErrorCode.INGEST_BRANCH_NOT_FOUND, 404, False, "分支不存在"),
    ErrorCode.INGEST_IGNORE_ALL: ErrorSpec(ErrorCode.INGEST_IGNORE_ALL, 422, False, "忽略规则过滤后没有可导入的 Markdown"),
    ErrorCode.INGEST_ZIP_INVALID: ErrorSpec(ErrorCode.INGEST_ZIP_INVALID, 422, False, "ZIP 文件无效或包含不安全条目"),
    ErrorCode.INGEST_ZIP_LIMIT: ErrorSpec(ErrorCode.INGEST_ZIP_LIMIT, 413, False, "ZIP 超过导入上限"),
    ErrorCode.INGEST_JOB_NOT_FOUND: ErrorSpec(ErrorCode.INGEST_JOB_NOT_FOUND, 404, False, "导入任务不存在"),
    ErrorCode.INGEST_JOB_NOT_RETRYABLE: ErrorSpec(ErrorCode.INGEST_JOB_NOT_RETRYABLE, 409, False, "该导入任务当前不可重试"),
    ErrorCode.INGEST_PARTIAL: ErrorSpec(ErrorCode.INGEST_PARTIAL, 206, True, "部分文件导入失败"),
    ErrorCode.INGEST_SNAPSHOT_STALE: ErrorSpec(ErrorCode.INGEST_SNAPSHOT_STALE, 409, True, "快照已过期，请先同步"),
    ErrorCode.INGEST_CONCURRENCY_LIMIT: ErrorSpec(ErrorCode.INGEST_CONCURRENCY_LIMIT, 429, True, "已有导入任务正在运行"),
    # §2.5 复盘与交付
    ErrorCode.ACTIVE_RUN_EXISTS: ErrorSpec(ErrorCode.ACTIVE_RUN_EXISTS, 409, False, "该周期已有正在运行的复盘"),
    ErrorCode.REVIEW_PERIOD_INVALID: ErrorSpec(ErrorCode.REVIEW_PERIOD_INVALID, 422, False, "周期格式不合法"),
    ErrorCode.REVIEW_PERIOD_TOO_OLD: ErrorSpec(ErrorCode.REVIEW_PERIOD_TOO_OLD, 422, False, "该周期超出可补算范围"),
    ErrorCode.REVIEW_NO_SOURCE_DATA: ErrorSpec(ErrorCode.REVIEW_NO_SOURCE_DATA, 422, False, "该周期没有任何素材"),
    ErrorCode.REVIEW_GATE_FAILED: ErrorSpec(ErrorCode.REVIEW_GATE_FAILED, 422, False, "内容未通过质量门禁"),
    ErrorCode.REVIEW_REVISION_LIMIT: ErrorSpec(ErrorCode.REVIEW_REVISION_LIMIT, 422, False, "修订轮次已用尽"),
    ErrorCode.REVIEW_HUMAN_REQUIRED: ErrorSpec(ErrorCode.REVIEW_HUMAN_REQUIRED, 409, False, "该复盘需要人工审批"),
    ErrorCode.REVIEW_ALREADY_DECIDED: ErrorSpec(ErrorCode.REVIEW_ALREADY_DECIDED, 409, False, "该复盘已审批"),
    ErrorCode.REVIEW_TOPIC_NEEDS_CONFIRM: ErrorSpec(ErrorCode.REVIEW_TOPIC_NEEDS_CONFIRM, 409, False, "存在待确认的专题条目"),
    ErrorCode.REVIEW_VERSION_NOT_FOUND: ErrorSpec(ErrorCode.REVIEW_VERSION_NOT_FOUND, 404, False, "指定的复盘版本不存在"),
    ErrorCode.DELIVERY_GIT_UNAVAILABLE: ErrorSpec(ErrorCode.DELIVERY_GIT_UNAVAILABLE, 503, True, "Git 服务暂时不可用"),
    ErrorCode.DELIVERY_CREDENTIAL_INVALID: ErrorSpec(ErrorCode.DELIVERY_CREDENTIAL_INVALID, 422, False, "交付凭据无效"),
    ErrorCode.DELIVERY_PR_CONFLICT: ErrorSpec(ErrorCode.DELIVERY_PR_CONFLICT, 409, False, "交付分支或 PR 存在冲突"),
    ErrorCode.DELIVERY_NOOP: ErrorSpec(ErrorCode.DELIVERY_NOOP, 200, False, "内容未变化，无需交付"),
    ErrorCode.DELIVERY_NOT_FOUND: ErrorSpec(ErrorCode.DELIVERY_NOT_FOUND, 404, False, "交付记录不存在"),
    ErrorCode.GIT_MARKER_BROKEN: ErrorSpec(ErrorCode.GIT_MARKER_BROKEN, 409, False, "自动区域标记缺失或被破坏"),
    ErrorCode.GIT_FORCE_PUSH_DENIED: ErrorSpec(ErrorCode.GIT_FORCE_PUSH_DENIED, 403, False, "不允许强制推送"),
    ErrorCode.GIT_PROVIDER_UNSUPPORTED: ErrorSpec(ErrorCode.GIT_PROVIDER_UNSUPPORTED, 422, False, "不支持的 Git 提供方"),
    ErrorCode.WEBHOOK_SIGNATURE_INVALID: ErrorSpec(ErrorCode.WEBHOOK_SIGNATURE_INVALID, 401, False, "webhook 签名校验失败"),
    ErrorCode.WEBHOOK_REPLAY: ErrorSpec(ErrorCode.WEBHOOK_REPLAY, 200, False, "该 webhook 事件已处理"),
    # §2.6 工具与模型依赖
    ErrorCode.TOOL_NOT_FOUND: ErrorSpec(ErrorCode.TOOL_NOT_FOUND, 404, False, "工具不存在"),
    ErrorCode.TOOL_NOT_ALLOWED: ErrorSpec(ErrorCode.TOOL_NOT_ALLOWED, 403, False, "当前阶段不允许调用该工具"),
    ErrorCode.TOOL_TIMEOUT: ErrorSpec(ErrorCode.TOOL_TIMEOUT, 504, True, "工具调用超时"),
    ErrorCode.TOOL_ARGS_INVALID: ErrorSpec(ErrorCode.TOOL_ARGS_INVALID, 422, False, "工具参数不合法"),
    ErrorCode.TOOL_DRIFT: ErrorSpec(ErrorCode.TOOL_DRIFT, 503, False, "工具声明与实现不一致，已禁用"),
    ErrorCode.SEARCH_VECTOR_TIMEOUT: ErrorSpec(ErrorCode.SEARCH_VECTOR_TIMEOUT, 200, False, "向量检索超时，已降级为关键词检索"),
    ErrorCode.SEARCH_EMPTY_QUERY: ErrorSpec(ErrorCode.SEARCH_EMPTY_QUERY, 422, False, "查询内容为空"),
    ErrorCode.SEARCH_TOO_MANY_TERMS: ErrorSpec(ErrorCode.SEARCH_TOO_MANY_TERMS, 422, False, "查询词过多"),
    ErrorCode.LLM_UNAVAILABLE: ErrorSpec(ErrorCode.LLM_UNAVAILABLE, 503, True, "模型服务暂时不可用"),
    ErrorCode.LLM_RATE_LIMITED: ErrorSpec(ErrorCode.LLM_RATE_LIMITED, 429, True, "模型服务限流"),
    ErrorCode.EMBEDDING_UNAVAILABLE: ErrorSpec(ErrorCode.EMBEDDING_UNAVAILABLE, 503, True, "向量服务暂时不可用"),
    ErrorCode.EMBEDDING_PROFILE_MISMATCH: ErrorSpec(ErrorCode.EMBEDDING_PROFILE_MISMATCH, 409, False, "向量维度与当前配置不一致"),
    ErrorCode.MODEL_OUTPUT_INVALID: ErrorSpec(ErrorCode.MODEL_OUTPUT_INVALID, 422, True, "模型输出格式不合法"),
    ErrorCode.PROVIDER_KEY_MISSING: ErrorSpec(ErrorCode.PROVIDER_KEY_MISSING, 422, False, "尚未配置模型凭据"),
    # §2.7 管理与删除
    ErrorCode.ADMIN_REQUIRED: ErrorSpec(ErrorCode.ADMIN_REQUIRED, 403, False, "需要平台管理员权限"),
    ErrorCode.ADMIN_USER_NOT_FOUND: ErrorSpec(ErrorCode.ADMIN_USER_NOT_FOUND, 404, False, "用户不存在"),
    ErrorCode.ADMIN_USER_DISABLED: ErrorSpec(ErrorCode.ADMIN_USER_DISABLED, 409, False, "该用户已被禁用"),
    ErrorCode.ADMIN_INVITE_LIMIT: ErrorSpec(ErrorCode.ADMIN_INVITE_LIMIT, 429, True, "邀请码创建过于频繁"),
    ErrorCode.ADMIN_SYSTEM_CONFIG_LOCKED: ErrorSpec(ErrorCode.ADMIN_SYSTEM_CONFIG_LOCKED, 409, False, "该配置由部署环境锁定"),
    ErrorCode.EXPORT_NOT_READY: ErrorSpec(ErrorCode.EXPORT_NOT_READY, 409, True, "导出任务尚未完成"),
    ErrorCode.EXPORT_EXPIRED: ErrorSpec(ErrorCode.EXPORT_EXPIRED, 410, False, "导出文件已过期"),
    ErrorCode.EXPORT_LIMIT: ErrorSpec(ErrorCode.EXPORT_LIMIT, 429, True, "导出请求过于频繁"),
    ErrorCode.DELETE_ALREADY_REQUESTED: ErrorSpec(ErrorCode.DELETE_ALREADY_REQUESTED, 409, False, "已存在删除请求"),
    ErrorCode.DELETE_IN_COOLING: ErrorSpec(ErrorCode.DELETE_IN_COOLING, 409, False, "删除请求处于冷却期"),
    ErrorCode.DELETE_NOT_CANCELLABLE: ErrorSpec(ErrorCode.DELETE_NOT_CANCELLABLE, 409, False, "删除请求已不可撤销"),
}


def spec(code: ErrorCode) -> ErrorSpec:
    return ERROR_SPECS[code]
