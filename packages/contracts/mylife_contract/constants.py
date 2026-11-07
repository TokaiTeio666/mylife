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
