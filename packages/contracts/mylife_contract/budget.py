"""预算契约（ARCHITECTURE §9.4；API-CONTRACTS §4.4/§8.3）。

`Budget` 是 run 级上限；`BudgetUsage` 是响应侧累计用量。预算跨重试与修订累计，
不允许每步重置。数值默认值见 `constants.DEFAULT_BUDGET_BY_KIND`。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from mylife_contract.common import ApiModel


class BudgetState(StrEnum):
    OK = "ok"
    WARN = "warn"
    EXCEEDED = "exceeded"


class Budget(ApiModel):
    """run 级 token/货币上限。"""

    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    warn_ratio: float = Field(default=0.8, gt=0, le=1)
    hard_stop_ratio: float = Field(default=1.0, gt=0, le=1)

    @model_validator(mode="after")
    def _ratios(self) -> Budget:
        if not self.warn_ratio < self.hard_stop_ratio:
            raise ValueError("warn_ratio must be less than hard_stop_ratio")
        return self


class BudgetUsage(ApiModel):
    """响应侧累计用量；ratio 取各维度最大值。"""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    ratio: float = Field(default=0.0, ge=0)
    state: BudgetState = BudgetState.OK
