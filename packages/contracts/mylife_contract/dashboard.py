"""仪表盘契约（API-CONTRACTS §3.8；AC-45…AC-47）。"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from mylife_contract.common import ApiModel, UtcDateTime
from mylife_contract.review import ReviewSummary

DashboardRange = Literal["7d", "30d", "90d", "year"]
TaskStatus = Literal["open", "done", "carried", "dropped"]


class DashboardLatest(ApiModel):
    daily: ReviewSummary | None = None
    weekly: ReviewSummary | None = None
    yearly: ReviewSummary | None = None
    generated_at: UtcDateTime
    completion_rate: float = 0.0
    open_tasks: int = 0


class TaskItem(ApiModel):
    id: UUID
    text: str
    status: TaskStatus
    first_seen: date
    last_seen: date
    days_open: int = 0
    done_date: date | None = None
    source_path: str | None = None
    line_number: int | None = None
    occurrence_count: int = 0


class TaskCounts(ApiModel):
    open: int = 0
    carried: int = 0
    done_today: int = 0


class TaskList(ApiModel):
    items: list[TaskItem] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    counts: TaskCounts = Field(default_factory=TaskCounts)


class DayCell(ApiModel):
    date: date
    has_daily: bool = False
    daily_status: str | None = None
    degraded: bool = False
    open_tasks: int = 0


class MonthCell(ApiModel):
    month: int
    has_weekly: bool = False
    weekly_period_key: str | None = None
    completion_rate: float | None = None


class CalendarResponse(ApiModel):
    year: int
    months: list[MonthCell] = Field(default_factory=list)
    days: list[DayCell] = Field(default_factory=list)


class DashboardTaskStats(ApiModel):
    completed: int = 0
    total: int = 0
    carried_over: int = 0


class DashboardReviewStats(ApiModel):
    daily: int = 0
    weekly: int = 0
    yearly: int = 0


class DashboardTokens(ApiModel):
    prompt: int = 0
    completion: int = 0


class DashboardStats(ApiModel):
    range: DashboardRange
    completion_rate: float = 0.0
    tasks: DashboardTaskStats = Field(default_factory=DashboardTaskStats)
    reviews: DashboardReviewStats = Field(default_factory=DashboardReviewStats)
    no_evidence_ratio: float = 0.0
    degraded_rate: float = 0.0
    tokens: DashboardTokens = Field(default_factory=DashboardTokens)
