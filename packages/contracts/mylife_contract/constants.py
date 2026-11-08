"""常量：Cookie/Header 名称、限额、能力矩阵、哨兵文案。

对应 API-CONTRACTS.md §1.3/§1.9/§8.3、ARCHITECTURE.md §5.1/§9.4。
"""

from __future__ import annotations

from typing import Final

from mylife_contract.enums import WorkspaceRole

# --- Cookie 与请求头（API-CONTRACTS §1.3/§1.9）---
COOKIE_SESSION: Final = "mylife_session"
COOKIE_CSRF: Final = "mylife_csrf"
HEADER_CSRF: Final = "X-CSRF-Token"
HEADER_REQUEST_ID: Final = "X-Request-Id"
HEADER_IDEMPOTENCY_KEY: Final = "Idempotency-Key"
HEADER_API_VERSION: Final = "X-API-Version"
HEADER_RATE_LIMIT_LIMIT: Final = "X-RateLimit-Limit"
HEADER_RATE_LIMIT_REMAINING: Final = "X-RateLimit-Remaining"
HEADER_RATE_LIMIT_RESET: Final = "X-RateLimit-Reset"
HEADER_RETRY_AFTER: Final = "Retry-After"
HEADER_IDEMPOTENT_REPLAYED: Final = "Idempotent-Replayed"
HEADER_LAST_EVENT_ID: Final = "Last-Event-ID"

API_VERSION: Final = "1"
API_PREFIX: Final = "/api/v1"
REQUEST_ID_PREFIX: Final = "req_"

# --- 密码与会话（API-CONTRACTS §1.3）---
PASSWORD_MIN_LENGTH: Final = 8
PASSWORD_MAX_LENGTH: Final = 128
EMAIL_MAX_LENGTH: Final = 254
DISPLAY_NAME_MAX_LENGTH: Final = 64
SESSION_TOKEN_BYTES: Final = 32
SESSION_TTL_DAYS: Final = 30
SESSION_IDLE_DAYS: Final = 7

# --- 邀请码（API-CONTRACTS §3.1.1）---
INVITE_CODE_MIN_LENGTH: Final = 8
INVITE_CODE_MAX_LENGTH: Final = 32

# --- 限额（API-CONTRACTS §3.1.5 Limits）---
DEFAULT_LIMITS: Final[dict[str, object]] = {
    "review_concurrency": 2,
    "daily_budget": {"input_tokens": 100_000, "output_tokens": 20_000},
    "weekly_budget": {"input_tokens": 300_000, "output_tokens": 60_000},
    "yearly_budget": {"input_tokens": 1_000_000, "output_tokens": 200_000},
    "monthly_budget": {"input_tokens": 5_000_000, "output_tokens": 1_000_000},
    "max_zip_bytes": 209_715_200,
    "max_search_top_k": 50,
    "max_workspaces_per_user": 5,
}

# --- 修订轮数上限（ARCHITECTURE §4.4）---
REVISION_LIMIT: Final[dict[str, int]] = {"daily": 2, "weekly": 2, "yearly": 3}

# --- 预算默认值（决策 5B；ARCHITECTURE §9.4 / API-CONTRACTS §4.4）---
DEFAULT_BUDGET_BY_KIND: Final[dict[str, dict[str, int]]] = {
    "daily": {"max_input_tokens": 100_000, "max_output_tokens": 20_000},
    "weekly": {"max_input_tokens": 300_000, "max_output_tokens": 60_000},
    "yearly": {"max_input_tokens": 1_000_000, "max_output_tokens": 200_000},
}

CHAT_DEFAULT_BUDGET: Final[dict[str, int]] = {
    "max_input_tokens": 32_000,
    "max_output_tokens": 4_000,
}

#: workspace 日配额 = 月配额 / 20（API-CONTRACTS §8.3）。
WORKSPACE_DAILY_DIVISOR: Final = 20

#: run 内按角色的规划配额（ARCHITECTURE §9.4；输出为硬上限）。
ROLE_BUDGETS: Final[dict[str, dict[str, int]]] = {
    "planner": {"max_input_tokens": 4_000, "max_output_tokens": 1_000},
    "writer": {"max_input_tokens": 20_000, "max_output_tokens": 6_000},
    "reviewer": {"max_input_tokens": 12_000, "max_output_tokens": 2_000},
    "worker": {"max_input_tokens": 0, "max_output_tokens": 0},
}

#: learning_summary 四要素（AC-35）。
LEARNING_SUMMARY_FIELDS: Final[tuple[str, ...]] = (
    "topic",
    "concept_or_method",
    "output",
    "open_questions",
)

#: 任务文本归一化的中文标点 → 半角映射（DATA-MODEL §7.3）。
PUNCT_NORMALIZATION: Final[dict[str, str]] = {
    "，": ",",
    "。": ".",
    "！": "!",
    "？": "?",
    "；": ";",
    "：": ":",
    "（": "(",
    "）": ")",
    "【": "[",
    "】": "]",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "、": ",",
}

# --- 登录限流（API-CONTRACTS §8.1）---
LOGIN_MAX_FAILURES_PER_EMAIL_IP: Final = 5
LOGIN_FAILURE_WINDOW_SECONDS: Final = 900
LOGIN_LOCK_SECONDS: Final = 900
LOGIN_MAX_PER_IP_PER_HOUR: Final = 20
REGISTER_MAX_PER_IP_PER_HOUR: Final = 3

# --- 能力矩阵（API-CONTRACTS §3.1.5 capabilities）---
CAPABILITIES_BY_ROLE: Final[dict[WorkspaceRole, tuple[str, ...]]] = {
    WorkspaceRole.OWNER: ("chat", "review_manual", "git_delivery", "export", "settings"),
    WorkspaceRole.ADMIN: ("chat", "review_manual", "export"),
    WorkspaceRole.MEMBER: ("chat",),
}

# --- 哨兵文案（ARCHITECTURE §5.1；前端不得改写）---
SENTINELS: Final[dict[str, str]] = {
    "today_tasks": "今日待办：无记录",
    "learning_summary": "学习内容摘要：本日无学习内容记录",
    "journal_events": "日记事件摘要：本日无日记事件记录",
    "mistakes": "做错的事：本日无明显错误记录",
    "methods": "正确方法论：本日无新增方法论记录",
    "tomorrow_actions": "明日行动：无（未找到可执行依据）",
    "one_line_summary": "一句话总结：本日无可总结内容",
    "completion_rate": "本周完成率：无记录",
    "recurring_mistakes": "重复错误：本周无记录",
    "method_landing": "方法论落地：本周无记录",
    "next_week_actions": "下周行动：无（未找到可执行依据）",
    "learning_map": "年度学习地图：本年无记录",
    "event_timeline": "事件线：本年无记录",
    "top_mistakes": "错误 Top10：本年无记录",
    "method_system": "方法论体系：本年无记录",
    "next_year_direction": "下一年方向：本年无记录",
    # 对话无依据哨兵（FR-D6 / AC-27）
    "chat_no_evidence": "没有找到相关记录",
}

#: daily 7 章，ordinal 1–7（ARCHITECTURE §5.1 唯一权威）。
DAILY_SECTIONS: Final[tuple[tuple[int, str, str, int], ...]] = (
    (1, "today_tasks", "今日待办", 12),
    (2, "learning_summary", "学习内容摘要", 12),
    (3, "journal_events", "日记事件摘要", 12),
    (4, "mistakes", "做错的事", 12),
    (5, "methods", "正确方法论", 12),
    (6, "tomorrow_actions", "明日行动", 12),
    (7, "one_line_summary", "一句话总结", 1),
)

WEEKLY_SECTIONS: Final[tuple[tuple[int, str, str, int], ...]] = (
    (1, "completion_rate", "任务完成率", 12),
    (2, "recurring_mistakes", "重复错误", 12),
    (3, "method_landing", "方法论落地", 12),
    (4, "next_week_actions", "下周行动", 12),
)

YEARLY_SECTIONS: Final[tuple[tuple[int, str, str, int], ...]] = (
    (1, "learning_map", "年度学习地图", 20),
    (2, "event_timeline", "事件线", 20),
    (3, "top_mistakes", "错误 Top10", 20),
    (4, "method_system", "方法论体系", 20),
    (5, "next_year_direction", "下一年方向", 10),
)

SECTION_ORDINALS: Final[dict[str, tuple[tuple[int, str, str, int], ...]]] = {
    "daily": DAILY_SECTIONS,
    "weekly": WEEKLY_SECTIONS,
    "yearly": YEARLY_SECTIONS,
}

#: 全局证据上限（ARCHITECTURE §9.1 / FX-01）。
MAX_EVIDENCE_PER_RUN: Final = 80

# --- Phase 4 交付（ARCHITECTURE §8；DIRECTORY-CONSTRAINTS §4）---
AUTO_MARKER_START: Final = "<!-- mylife:auto:start -->"
AUTO_MARKER_END: Final = "<!-- mylife:auto:end -->"

#: 专题 slug → 文件名（DB 英文 ↔ 交付文件中文）。
TOPIC_FILE_NAMES: Final[dict[str, str]] = {
    "errors": "错误清单",
    "methods": "正确方法论",
    "actions": "行动清单",
}

#: 交付仓库中的固定产物（相对 reviews_root）。
INDEX_PATH: Final = "索引.md"

#: PR 正文六段（ARCHITECTURE §8.2）。
PR_BODY_SECTIONS: Final[tuple[str, ...]] = (
    "30 秒结论",
    "章节自检",
    "证据前 10 条",
    "变更文件",
    "风险与回滚",
    "GateResult",
)

#: 交付分支前缀（ARCHITECTURE §8.2）。
REVIEW_BRANCH_PREFIX: Final = "review"

# --- Phase 4 删除（SEC-27 / DATA-MODEL §9.2 并集，PHASE4-SPEC D19）---
DELETION_PURGE_TABLES: Final[tuple[str, ...]] = (
    "documents",
    "document_chunks",
    "document_links",
    "tasks",
    "task_occurrences",
    "reviews",
    "review_versions",
    "review_sections",
    "review_entries",
    "review_citations",
    "review_search",
    "review_confirmations",
    "topic_items",
    "topic_occurrences",
    "topic_files",
    "conversations",
    "messages",
    "agent_runs",
    "run_steps",
    "run_events",
    "evidence_items",
    "source_snapshots",
    "snapshot_documents",
    "document_changes",
    "delivery_records",
    "delivery_events",
    "provider_credentials",
    "embedding_profiles",
    "export_jobs",
    "workspace_settings",
    "workspace_schedules",
    "git_connections",
    "job_runs",
    "idempotency_keys",
    "outbox_events",
    "usage_events",
    "sessions",
    "workspace_members",
)

DELETION_NOTICE: Final[dict[str, object]] = {
    "will_delete": list(DELETION_PURGE_TABLES) + ["vectors", "minio_objects", "redis_keys"],
    "will_retain": ["audit_logs(脱敏元数据)", "merged_git_history"],
    "notice": (
        "已合并到 Git 的历史提交会被保留且仍可通过 git log 读取；"
        "mylife 不承诺从 Git 历史抹除，仅保证 mylife 侧数据删除。"
    ),
}


# --- Phase 5 审计五类（AC-07；PHASE5-SPEC D18）---
#: AC-07 要求“恰好 5 类 action 各至少 1 行”；族内任一 action 命中即算该类。
AUDIT_AC07_FAMILIES: Final[dict[str, tuple[str, ...]]] = {
    "login": ("user.login",),
    "import": ("ingest.started",),
    "deletion": ("deletion.requested",),
    "config": (
        "settings.llm.updated",
        "settings.embedding.updated",
        "settings.schedule.updated",
        "settings.git.updated",
        "settings.retrieval.updated",
        "admin.system_config.updated",
    ),
    "review": ("review.created",),
}

# --- Phase 5 导出（AC-55 / AC-64；PHASE5-SPEC §6）---
#: 导出包保留期（天）；到期由 dispatcher 置 `expired` 并删除文件。
EXPORT_TTL_DAYS: Final = 7
#: 每 workspace 每日导出次数上限（API-CONTRACTS §8.1）。
EXPORT_DAILY_LIMIT: Final = 3
#: 导出包内的固定文件名。
EXPORT_MANIFEST_NAME: Final = "manifest.json"
EXPORT_SETTINGS_NAME: Final = "settings.json"
EXPORT_USAGE_NAME: Final = "usage.csv"
EXPORT_AUDIT_NAME: Final = "audit.jsonl"

# --- Phase 5 蓝绿换维（AC-50；PHASE5-SPEC D8/D9）---
#: 自动切换阈值；AC-50 的证据断言使用 1.0。
EMBEDDING_COVERAGE_THRESHOLD: Final = 0.995
#: 旧 chunk 表在切换后的保留期（天）。
EMBEDDING_RETIRE_DAYS: Final = 7

# --- Phase 5 法务页（AC-64；PHASE5-SPEC D21）---
LEGAL_SLUGS: Final[tuple[str, ...]] = ("privacy", "terms")
