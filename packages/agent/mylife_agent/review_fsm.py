"""复盘 FSM 迁移表（ARCHITECTURE §4.1/§4.2；API-CONTRACTS §7.1）。

纯数据 + 校验函数：状态推进由 `review_runner` 执行，落库走 CAS。
"""

from __future__ import annotations

from dataclasses import dataclass

REVIEW_STATES = (
    "created",
    "snapshotting",
    "planning",
    "retrieving",
    "drafting",
    "reviewing",
    "revising",
    "human_gate",
    "delivering",
    "succeeded",
    "retry_wait",
    "needs_human",
    "security_abort",
    "failed",
    "cancelled",
)

TERMINAL_STATES = frozenset(
    {"succeeded", "failed", "cancelled", "needs_human", "security_abort"}
)

ACTIVE_STATES = frozenset(set(REVIEW_STATES) - TERMINAL_STATES)

#: 复盘允许的状态集合（chat 的 `verifying` 不在此列）。
REVIEW_ONLY_STATES = frozenset(REVIEW_STATES)

STATE_TIMEOUTS: dict[str, int] = {
    "created": 30,
    "snapshotting": 90,
    "planning": 180,
    "retrieving": 420,
    "drafting": 240,
    "reviewing": 180,
    "revising": 180,
    "delivering": 180,
    "retry_wait": 60,
}

STATE_MAX_ATTEMPTS: dict[str, int] = {
    "created": 1,
    "snapshotting": 3,
    "planning": 2,
    "retrieving": 3,
    "drafting": 2,
    "reviewing": 2,
    "revising": 1,
    "delivering": 3,
    "retry_wait": 3,
}


@dataclass(frozen=True, slots=True)
class Transition:
    to_state: str
    reasons: tuple[str, ...]


TRANSITIONS: dict[str, tuple[Transition, ...]] = {
    "created": (
        Transition("snapshotting", ("admit_ok",)),
        Transition("failed", ("invalid_initial_state",)),
        Transition("cancelled", ("user_cancel",)),
    ),
    "snapshotting": (
        Transition("planning", ("snapshot_ok",)),
        Transition("needs_human", ("attempts_exhausted",)),
        Transition("cancelled", ("user_cancel",)),
    ),
    "planning": (
        Transition("retrieving", ("plan_valid",)),
        Transition("needs_human", ("schema_retry_exhausted",)),
        Transition("failed", ("budget_exceeded", "unhandled")),
        Transition("cancelled", ("user_cancel",)),
    ),
    "retrieving": (
        Transition("drafting", ("quorum_ok", "quorum_partial_degraded")),
        Transition("needs_human", ("required_missing", "attempts_exhausted")),
        Transition("failed", ("budget_exceeded", "unhandled")),
    ),
    "drafting": (
        Transition("reviewing", ("draft_schema_ok",)),
        Transition("needs_human", ("schema_retry_exhausted",)),
        Transition("failed", ("budget_exceeded", "unhandled")),
    ),
    "reviewing": (
        Transition("delivering", ("gate_pass",)),
        Transition("revising", ("blocking_found",)),
        Transition(
            "human_gate",
            (
                "yearly_default",
                "reviewer_disagreement",
                "reviewer_unavailable",
                "revision_exhausted",
                "conflict",
            ),
        ),
        Transition("security_abort", ("tenant_leak", "fabrication")),
        Transition("failed", ("budget_exceeded", "unhandled")),
    ),
    "revising": (
        Transition("drafting", ("revised",)),
        Transition("retrieving", ("missing_evidence",)),
        Transition("human_gate", ("revision_exhausted", "reviewer_unavailable")),
        Transition("failed", ("budget_exceeded", "unhandled")),
    ),
    "human_gate": (
        Transition("delivering", ("human_approved",)),
        Transition("cancelled", ("human_rejected",)),
        Transition("needs_human", ("gate_unavailable",)),
    ),
    "delivering": (
        Transition("succeeded", ("local_only", "noop")),
        Transition("retry_wait", ("transient_failure",)),
        Transition("needs_human", ("attempts_exhausted",)),
    ),
    "retry_wait": (
        Transition("delivering", ("retry",)),
        Transition("needs_human", ("attempts_exhausted",)),
    ),
}


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def is_active(state: str) -> bool:
    return state in ACTIVE_STATES


def can_transition(from_state: str, to_state: str, reason: str) -> bool:
    for transition in TRANSITIONS.get(from_state, ()):
        if transition.to_state == to_state and reason in transition.reasons:
            return True
    return False


def allowed_targets(from_state: str) -> tuple[str, ...]:
    return tuple(transition.to_state for transition in TRANSITIONS.get(from_state, ()))


def validate_review_state(state: str) -> bool:
    return state in REVIEW_ONLY_STATES
