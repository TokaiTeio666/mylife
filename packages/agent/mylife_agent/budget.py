"""预算策略（ARCHITECTURE §9.4；AC-66；纯逻辑，无 IO）。

`BudgetGuard` 只做判定：给定已用与上限，返回 ok/warn/exceeded 与决策。
累计与持久化由 API 层的 `services_budget` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mylife_contract.budget import Budget, BudgetState, BudgetUsage
from mylife_contract.constants import (
    CHAT_DEFAULT_BUDGET,
    DEFAULT_BUDGET_BY_KIND,
    WORKSPACE_DAILY_DIVISOR,
)


class BudgetExceeded(RuntimeError):
    """预算已硬停：不得再构造/调用 LLM provider。"""

    def __init__(self, scope: str, usage: BudgetUsage, limit: Budget) -> None:
        self.scope = scope
        self.usage = usage
        self.limit = limit
        super().__init__(f"budget exceeded ({scope})")


def budget_for_kind(kind: str) -> Budget:
    """按 kind 取默认预算；chat 使用独立默认值。"""
    if kind == "chat":
        return Budget(**CHAT_DEFAULT_BUDGET)
    values = DEFAULT_BUDGET_BY_KIND.get(kind)
    if values is None:
        raise ValueError(f"unknown run kind: {kind}")
    return Budget(**values)


def workspace_limits() -> dict[str, Budget]:
    """workspace 月/日配额（日 = 月 / 20）。"""
    monthly = Budget(max_input_tokens=5_000_000, max_output_tokens=1_000_000)
    daily = Budget(
        max_input_tokens=5_000_000 // WORKSPACE_DAILY_DIVISOR,
        max_output_tokens=1_000_000 // WORKSPACE_DAILY_DIVISOR,
    )
    return {"workspace_monthly": monthly, "workspace_daily": daily}


def usage_of(
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    budget: Budget,
) -> BudgetUsage:
    """按 `max(各维度比例)` 计算 ratio 与 state（4 位小数）。"""
    ratios = [
        input_tokens / budget.max_input_tokens if budget.max_input_tokens else 0.0,
        output_tokens / budget.max_output_tokens if budget.max_output_tokens else 0.0,
    ]
    if budget.max_cost_usd:
        ratios.append(cost_usd / budget.max_cost_usd)
    ratio = round(max(ratios), 4)

    if ratio >= budget.hard_stop_ratio or (
        input_tokens >= budget.max_input_tokens
        or output_tokens >= budget.max_output_tokens
    ):
        state = BudgetState.EXCEEDED
    elif ratio >= budget.warn_ratio:
        state = BudgetState.WARN
    else:
        state = BudgetState.OK

    return BudgetUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost_usd, 4),
        ratio=ratio,
        state=state,
    )


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    state: BudgetState
    scope: Literal["run", "workspace_daily", "workspace_monthly"]
    usage: BudgetUsage
    should_warn: bool
    should_stop: bool


def evaluate(
    *,
    used_input: int,
    used_output: int,
    used_cost: float,
    budget: Budget,
    scope: Literal["run", "workspace_daily", "workspace_monthly"] = "run",
    already_warned: bool = False,
) -> BudgetDecision:
    usage = usage_of(
        input_tokens=used_input,
        output_tokens=used_output,
        cost_usd=used_cost,
        budget=budget,
    )
    return BudgetDecision(
        state=usage.state,
        scope=scope,
        usage=usage,
        should_warn=usage.state == BudgetState.WARN and not already_warned,
        should_stop=usage.state == BudgetState.EXCEEDED,
    )


def role_output_cap(role: str, run_remaining_output: int) -> int:
    """角色输出上限 = min(角色配额, run 剩余)，供 `LLMRequest.max_tokens`。"""
    from mylife_contract.constants import ROLE_BUDGETS

    role_cap = ROLE_BUDGETS.get(role, {}).get("max_output_tokens", run_remaining_output)
    if role_cap <= 0:
        return 0
    return max(0, min(role_cap, run_remaining_output))
