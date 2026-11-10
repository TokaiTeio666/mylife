"""预算 guard 与 FSM 表（AC-66；ARCHITECTURE §4.1/§9.4）。"""

from __future__ import annotations

import pytest
from mylife_agent.budget import (
    BudgetExceeded,
    budget_for_kind,
    evaluate,
    role_output_cap,
    usage_of,
    workspace_limits,
)
from mylife_agent.review_fsm import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    can_transition,
    is_active,
    is_terminal,
    validate_review_state,
)
from mylife_contract.budget import Budget, BudgetState


def test_budget_defaults_per_kind():
    assert budget_for_kind("daily").max_input_tokens == 100_000
    assert budget_for_kind("daily").max_output_tokens == 20_000
    assert budget_for_kind("monthly").max_input_tokens == 300_000
    assert budget_for_kind("yearly").max_output_tokens == 200_000
    chat = budget_for_kind("chat")
    assert chat.max_input_tokens == 32_000 and chat.max_output_tokens == 4_000
    with pytest.raises(ValueError):
        budget_for_kind("unknown")


def test_workspace_quota_is_month_over_twenty():
    limits = workspace_limits()
    assert limits["workspace_monthly"].max_input_tokens == 5_000_000
    assert limits["workspace_daily"].max_input_tokens == 250_000
    assert limits["workspace_daily"].max_output_tokens == 50_000


def test_usage_ratio_is_max_of_dimensions():
    budget = Budget(max_input_tokens=100, max_output_tokens=10)
    usage = usage_of(input_tokens=50, output_tokens=9, cost_usd=0, budget=budget)
    assert usage.ratio == 0.9
    assert usage.state == BudgetState.WARN
    assert usage.cost_usd == 0.0


def test_warn_then_stop_thresholds():
    budget = Budget(max_input_tokens=100, max_output_tokens=100)
    warn = evaluate(used_input=80, used_output=0, used_cost=0, budget=budget)
    assert warn.state == BudgetState.WARN
    assert warn.should_warn is True and warn.should_stop is False

    again = evaluate(
        used_input=85, used_output=0, used_cost=0, budget=budget, already_warned=True
    )
    assert again.should_warn is False

    stop = evaluate(used_input=100, used_output=0, used_cost=0, budget=budget)
    assert stop.state == BudgetState.EXCEEDED and stop.should_stop is True


def test_cost_dimension_can_trigger_stop():
    budget = Budget(max_input_tokens=1000, max_output_tokens=1000, max_cost_usd=1.0)
    decision = evaluate(
        used_input=0, used_output=0, used_cost=1.0, budget=budget, scope="workspace_daily"
    )
    assert decision.should_stop is True
    assert decision.scope == "workspace_daily"


def test_budget_exceeded_carries_scope():
    budget = Budget(max_input_tokens=1, max_output_tokens=1)
    decision = evaluate(used_input=1, used_output=0, used_cost=0, budget=budget)
    assert decision.should_stop
    error = BudgetExceeded("run", decision.usage, budget)
    assert error.scope == "run"


def test_role_output_cap_never_exceeds_run_remaining():
    assert role_output_cap("writer", 100_000) == 6_000
    assert role_output_cap("planner", 500) == 500
    assert role_output_cap("worker", 10_000) == 0


def test_fsm_terminal_and_active_sets():
    assert "needs_human" in TERMINAL_STATES
    assert "needs_human" not in ACTIVE_STATES
    assert "human_gate" in ACTIVE_STATES
    assert is_terminal("security_abort") and is_active("retrieving")
    assert not validate_review_state("verifying")  # chat 专用状态


def test_fsm_transitions_are_guarded_by_reason():
    assert can_transition("created", "snapshotting", "admit_ok")
    assert not can_transition("created", "snapshotting", "gate_pass")
    assert can_transition("reviewing", "security_abort", "tenant_leak")
    assert not can_transition("reviewing", "security_abort", "blocking_found")
    assert can_transition("reviewing", "human_gate", "yearly_default")
    assert can_transition("revising", "retrieving", "missing_evidence")
    assert can_transition("human_gate", "cancelled", "human_rejected")
    assert can_transition("delivering", "succeeded", "local_only")
    # 终态没有出边
    for state in TERMINAL_STATES:
        assert can_transition(state, "succeeded", "local_only") is False
