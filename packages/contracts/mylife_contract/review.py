"""复盘领域契约（ARCHITECTURE §5/§5.1；API-CONTRACTS §3.7/§4）。

本文件是 `SECTION_SCHEMAS` / `ITEM_SCHEMAS` 的唯一代码落点，启动时由
`review-profile.yml` 逐项校验（ARCHITECTURE §16.9）。所有模型 `extra="forbid"`。

字段名说明：§5.1 只给中文必填字段名；此处冻结英文键名（PHASE3-SPEC §5.2）。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from mylife_contract.budget import Budget
from mylife_contract.common import ApiModel, UtcDateTime

RunKind = Literal["daily", "monthly", "yearly"]
ReviewStatus = Literal[
    "draft",
    "in_review",
    "published",
    "superseded",
    "manual",
    "generating",
    "needs_human",
    "approved",
    "delivered",
    "failed",
    "security_abort",
]


# ---------------------------------------------------------------------------
# daily sections
# ---------------------------------------------------------------------------


class TaskRef(ApiModel):
    text: str = Field(min_length=1)
    task_id: str | None = None
    source_date: date | None = None
    source_path: str | None = None
    line_range: list[int] | None = None
    evidence_id: str | None = None


class TodayTasksSection(ApiModel):
    completed: list[TaskRef] = Field(default_factory=list)
    open: list[TaskRef] = Field(default_factory=list)
    carried_over: list[TaskRef] = Field(default_factory=list)


class LearningSummarySection(ApiModel):
    topic: str = Field(min_length=1)
    concept_or_method: str = Field(min_length=1)
    output: str = Field(min_length=1)
    open_questions: str = Field(min_length=1)
    field_evidence: dict[str, list[str]] = Field(default_factory=dict)


class JournalEvent(ApiModel):
    event: str = Field(min_length=1)
    person: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    emotion: str = Field(min_length=1)
    result: str = Field(min_length=1)


class JournalEventsSection(ApiModel):
    items: list[JournalEvent] = Field(default_factory=list)


class MistakeItem(ApiModel):
    event: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    cost: str = Field(min_length=1)
    correct_action: str = Field(min_length=1)
    recurrence_trigger: str = Field(min_length=1)


class MistakesSection(ApiModel):
    items: list[MistakeItem] = Field(default_factory=list)


class MethodItem(ApiModel):
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    applicable_scenario: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    boundaries: str = Field(min_length=1)
    landing_action: str = Field(min_length=1)
    verification_criteria: str = Field(min_length=1)


class MethodsSection(ApiModel):
    items: list[MethodItem] = Field(default_factory=list)


class ActionItem(ApiModel):
    text: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    verification: str = Field(min_length=1)


class TomorrowActionsSection(ApiModel):
    items: list[ActionItem] = Field(default_factory=list)


class OneLineSummarySection(ApiModel):
    summary: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# monthly sections
# ---------------------------------------------------------------------------


class LedgerRecalc(ApiModel):
    completed: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: float = Field(ge=0, le=1)


class CompletionRateSection(ApiModel):
    completed: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: float = Field(ge=0, le=1)
    ledger_recalc: LedgerRecalc


class RecurringMistakeItem(ApiModel):
    canonical_key: str = Field(min_length=1)
    occurrences: int = Field(ge=2)
    first_seen: date
    last_seen: date


class RecurringMistakesSection(ApiModel):
    top_n: int = Field(default=10, ge=1)
    items: list[RecurringMistakeItem] = Field(default_factory=list)


class MethodLandingItem(ApiModel):
    method: str = Field(min_length=1)
    action_count: int = Field(ge=0)
    verification_status: str = Field(min_length=1)


class MethodLandingSection(ApiModel):
    items: list[MethodLandingItem] = Field(default_factory=list)


class NextMonthActionsSection(ApiModel):
    items: list[ActionItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# yearly sections
# ---------------------------------------------------------------------------


class LearningMapSection(ApiModel):
    topics: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    related_months: list[str] = Field(default_factory=list)

    @field_validator("related_months")
    @classmethod
    def _months(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) != 7 or not item[:4].isdigit() or item[4] != "-":
                raise ValueError("related_months must match YYYY-MM")
            if not item[5:].isdigit() or not 1 <= int(item[5:]) <= 12:
                raise ValueError("related_months must match YYYY-MM")
        return value


class TimelineEvent(ApiModel):
    date: date
    event: str = Field(min_length=1)
    result: str = Field(min_length=1)


class EventTimelineSection(ApiModel):
    items: list[TimelineEvent] = Field(default_factory=list)

    @field_validator("items")
    @classmethod
    def _ordered(cls, value: list[TimelineEvent]) -> list[TimelineEvent]:
        dates = [item.date for item in value]
        if dates != sorted(dates):
            raise ValueError("event_timeline items must be sorted by date")
        return value


class TopMistakeItem(ApiModel):
    rank: int = Field(ge=1, le=10)
    item: str = Field(min_length=1)
    occurrences: int = Field(ge=1)
    cost: str = Field(min_length=1)
    source: str = Field(min_length=1)


class TopMistakesSection(ApiModel):
    items: list[TopMistakeItem] = Field(default_factory=list)

    @field_validator("items")
    @classmethod
    def _ranks(cls, value: list[TopMistakeItem]) -> list[TopMistakeItem]:
        ranks = [item.rank for item in value]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("top_mistakes ranks must be 1..n")
        return value


class MethodSystemItem(ApiModel):
    category: str = Field(min_length=1)
    name: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    verification: str = Field(min_length=1)


class MethodSystemSection(ApiModel):
    items: list[MethodSystemItem] = Field(default_factory=list)


class NextYearDirectionSection(ApiModel):
    direction: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    first_action: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# registries
# ---------------------------------------------------------------------------

SECTION_SCHEMAS: dict[str, dict[str, type[ApiModel]]] = {
    "daily": {
        "today_tasks": TodayTasksSection,
        "learning_summary": LearningSummarySection,
        "journal_events": JournalEventsSection,
        "mistakes": MistakesSection,
        "methods": MethodsSection,
        "tomorrow_actions": TomorrowActionsSection,
        "one_line_summary": OneLineSummarySection,
    },
    "monthly": {
        "completion_rate": CompletionRateSection,
        "recurring_mistakes": RecurringMistakesSection,
        "method_landing": MethodLandingSection,
        "next_month_actions": NextMonthActionsSection,
    },
    "yearly": {
        "learning_map": LearningMapSection,
        "event_timeline": EventTimelineSection,
        "top_mistakes": TopMistakesSection,
        "method_system": MethodSystemSection,
        "next_year_direction": NextYearDirectionSection,
    },
}


class ErrorItemFields(ApiModel):
    event: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    cost: str = Field(min_length=1)
    correct_action: str = Field(min_length=1)
    recurrence_trigger: str = Field(min_length=1)


class MethodItemFields(ApiModel):
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    applicable_scenario: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    boundaries: str = Field(min_length=1)
    landing_action: str = Field(min_length=1)
    verification_criteria: str = Field(min_length=1)


class ActionItemFields(ApiModel):
    text: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    verification: str = Field(min_length=1)


ITEM_SCHEMAS: dict[str, type[ApiModel]] = {
    "error": ErrorItemFields,
    "method": MethodItemFields,
    "action": ActionItemFields,
}


def validate_section(kind: str, section_key: str, payload: dict[str, Any]) -> ApiModel:
    model = SECTION_SCHEMAS.get(kind, {}).get(section_key)
    if model is None:
        raise KeyError(f"unknown section {kind}/{section_key}")
    return model.model_validate(payload)


def validate_entry(item_type: str, payload: dict[str, Any]) -> ApiModel:
    model = ITEM_SCHEMAS.get(item_type)
    if model is None:
        raise KeyError(f"unknown item_type {item_type}")
    return model.model_validate(payload)


# ---------------------------------------------------------------------------
# Plan / Evidence / Draft / Verdict
# ---------------------------------------------------------------------------


class SectionPlan(ApiModel):
    section_key: str
    ordinal: int = Field(ge=1)
    title: str
    required_fields: list[str] = Field(default_factory=list)
    evidence_task_ids: list[str] = Field(default_factory=list)
    max_evidence: int = Field(ge=1)
    required: bool = True


class RetrievalTask(ApiModel):
    task_id: str
    section_keys: list[str]
    tool: Literal[
        "search_notes", "read_note", "list_tasks", "get_review", "get_stats"
    ]
    args: dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    timeout_ms: int | None = Field(default=None, ge=1, le=20_000)
    max_results: int | None = Field(default=None, ge=1, le=50)


class CorrelationTarget(ApiModel):
    correlation_type: Literal[
        "PRIOR_REVIEW",
        "TOPIC_PAGE",
        "WIKILINK_NEIGHBORHOOD",
        "TAG_COHORT",
        "RECURRING_ERROR",
        "RECURRING_METHOD",
        "TASK_CARRY",
        "STATS_BASELINE",
    ]
    section_keys: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    max_items: int = Field(default=12, ge=1, le=20)


class Coverage(ApiModel):
    required_tasks: int = 0
    succeeded_tasks: int = 0
    failed_task_ids: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    status: Literal["ok", "partial", "none"] = "none"


class Missing(ApiModel):
    section_key: str
    reason: Literal[
        "no_evidence",
        "timeout",
        "disabled",
        "profile_mismatch",
        "io_error",
        "runtime_unavailable",
    ]
    detail: str = ""


class EvidenceItem(ApiModel):
    evidence_id: str
    source_type: Literal["document", "chunk", "review", "task", "stats"]
    document_id: UUID | None = None
    chunk_id: UUID | None = None
    source_path: str | None = None
    source_date: date | None = None
    occurred_on: date | None = None
    document_content_hash: str | None = None
    heading: str | None = None
    line_range: tuple[int, int] | None = None
    quote: str
    quote_hash: str
    tags: list[str] = Field(default_factory=list)


class EvidenceBundle(ApiModel):
    bundle_id: str
    run_id: UUID
    snapshot_id: UUID
    items: list[EvidenceItem] = Field(default_factory=list)
    coverage: dict[str, Coverage] = Field(default_factory=dict)
    missing: list[Missing] = Field(default_factory=list)
    degraded: bool = False


class Plan(ApiModel):
    plan_version: int = Field(ge=1)
    period_start: date
    period_end: date
    sections: list[SectionPlan]
    tasks: list[RetrievalTask]
    correlations: list[CorrelationTarget] = Field(default_factory=list)
    budget: Budget

    @model_validator(mode="after")
    def _period(self) -> Plan:
        if self.period_end < self.period_start:
            raise ValueError("period_end must be >= period_start")
        return self


class Claim(ApiModel):
    claim_id: str
    section_key: str
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    citation_indexes: list[int] = Field(default_factory=list)
    claim_type: Literal["fact", "task_status", "event", "mistake", "method", "action"] = "fact"


class DraftSection(ApiModel):
    section_key: str
    ordinal: int = Field(ge=1)
    content_md: str
    structured: dict[str, Any] = Field(default_factory=dict)
    claim_ids: list[str] = Field(default_factory=list)
    sentinel_used: bool = False
    degraded: bool = False


class Draft(ApiModel):
    draft_version: int = Field(ge=1)
    sections: list[DraftSection]
    claims: list[Claim] = Field(default_factory=list)


class GateIssue(ApiModel):
    gate: str
    severity: Literal["blocking", "warning"]
    code: str
    message: str
    section_key: str | None = None
    claim_id: str | None = None
    evidence_id: str | None = None
    must_fix: str | None = None


class ReviewVerdict(ApiModel):
    decision: Literal["APPROVE", "REVISE", "REJECT", "NEEDS_HUMAN"]
    blocking: list[GateIssue] = Field(default_factory=list)
    warnings: list[GateIssue] = Field(default_factory=list)
    revision_round: int = Field(default=0, ge=0)
    fingerprint: str


# ---------------------------------------------------------------------------
# Run options / API models
# ---------------------------------------------------------------------------


class RunOptions(ApiModel):
    max_revisions: int | None = Field(default=None, ge=0)
    require_human: bool = True
    model_profile: str = "default"
    budget: Budget | None = None
    retrieval_top_k: int = Field(default=20, ge=1, le=50)
    context_window_tokens: int = Field(default=32_000, ge=4_000, le=200_000)
    web_search_enabled: bool = False


class ReviewCreateRequest(ApiModel):
    kind: RunKind
    period_key: str = Field(min_length=4, max_length=20)
    timezone: str | None = Field(default=None, max_length=64)
    trigger: Literal["manual", "schedule", "api"] = "manual"
    options: RunOptions | None = None
    allow_empty: bool = False
    confirmation_token: str | None = Field(default=None, max_length=4096)


class ReviewCreateResponse(ApiModel):
    run_id: UUID
    review_id: UUID | None = None
    state: str = "created"
    existing_run_id: UUID | None = None
    version: int | None = None


class ReviewSummary(ApiModel):
    id: UUID
    kind: RunKind
    period_key: str
    title: str
    version: int
    status: ReviewStatus
    generated_at: UtcDateTime
    path: str | None = None
    degraded: bool = False
    delivery_status: Literal[
        "none", "pending", "pr_open", "merged", "noop", "blocked", "ui_diff"
    ] = "none"


class ReviewList(ApiModel):
    items: list[ReviewSummary]
    next_cursor: str | None = None
    has_more: bool = False


class ReviewSection(ApiModel):
    section_key: str
    ordinal: int
    content_md: str
    structured: dict[str, Any] = Field(default_factory=dict)


class ReviewCitation(ApiModel):
    claim_id: str
    evidence_id: str
    source_path: str | None = None
    source_date: date | None = None
    line_range: list[int] | None = None
    quote: str


class ReviewEntry(ApiModel):
    section_key: str
    item_type: Literal["error", "method", "action"]
    canonical_key: str
    fields: dict[str, Any] = Field(default_factory=dict)
    source_path: str | None = None
    line_range: list[int] | None = None
    confidence: float | None = None


class VersionRef(ApiModel):
    version: int
    content_hash: str
    git_sha: str | None = None
    generated_at: UtcDateTime
    is_current: bool


class ReviewDetail(ApiModel):
    id: UUID
    kind: RunKind
    period_key: str
    version: int
    current_version: int
    title: str
    content_md: str
    content_html: str
    sections: list[ReviewSection] = Field(default_factory=list)
    citations: list[ReviewCitation] = Field(default_factory=list)
    entries: list[ReviewEntry] = Field(default_factory=list)
    verdict: dict[str, Any] | None = None
    model: str | None = None
    snapshot_id: UUID | None = None
    generated_at: UtcDateTime
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    git_path: str | None = None
    versions: list[VersionRef] = Field(default_factory=list)
    superseded_by: int | None = None


class RollbackRequest(ApiModel):
    version: int = Field(ge=1)
    confirm: bool = False


class RollbackResponse(ApiModel):
    run_id: UUID
    target_version: int


class ApproveRequest(ApiModel):
    decision_note: str | None = Field(default=None, max_length=2000)
    edited_content_md: str | None = None
    confirm: bool | None = None


class RejectRequest(ApiModel):
    reason_code: Literal[
        "CONTENT_WRONG", "CITATIONS_WRONG", "TONE", "NOT_NEEDED", "OTHER"
    ]
    note: str | None = Field(default=None, max_length=2000)


class GateDecisionResponse(ApiModel):
    run_id: UUID
    state: str
    delivery_id: UUID | None = None
