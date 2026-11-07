"""Reviewer rubric R1–R9 与修订收敛（ARCHITECTURE §9.2/§4.4）。

R1/R2/R3/R5/R8/R9 由确定性规则判定；R4 是 LLM judge（advisory，仅 warning）；
R6/R7 产出 must_fix 建议。**LLM 综合分不作为放行条件**。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mylife_contract.constants import REVISION_LIMIT
from mylife_contract.review import GateIssue, ReviewVerdict

from mylife_agent.gates import GateContext, evaluate_gates, fingerprint, has_abort


@dataclass(frozen=True, slots=True)
class RubricResult:
    verdict: ReviewVerdict
    next_state: str  # delivering | revising | human_gate | security_abort


def decide(
    verdict: ReviewVerdict,
    *,
    kind: str,
    revision_round: int,
    previous_fingerprint: str | None = None,
    previous_blocking_count: int | None = None,
) -> RubricResult:
    """按 §4.4 三条件决定下一步状态。

    继续修订必须同时满足：轮次未超 + 指纹变化 + blocking 数下降。
    """
    abort = has_abort(verdict.blocking)
    if abort is not None:
        return RubricResult(verdict=verdict, next_state="security_abort")

    if not verdict.blocking:
        return RubricResult(verdict=verdict, next_state="delivering")

    limit = REVISION_LIMIT.get(kind, 0)
    round_ok = revision_round < limit
    fingerprint_changed = previous_fingerprint is None or (
        verdict.fingerprint != previous_fingerprint
    )
    count_decreased = previous_blocking_count is None or (
        len(verdict.blocking) < previous_blocking_count
    )
    if round_ok and fingerprint_changed and count_decreased:
        return RubricResult(verdict=verdict, next_state="revising")
    return RubricResult(verdict=verdict, next_state="human_gate")


def run_review(
    ctx: GateContext,
    *,
    revision_round: int = 0,
    previous_fingerprint: str | None = None,
    previous_blocking_count: int | None = None,
    resolved_owners: dict[str, str] | None = None,
    fact_consistency: float | None = None,
) -> RubricResult:
    """执行确定性门禁 + 可选事实一致性（advisory），并给出下一状态。"""
    verdict = evaluate_gates(ctx, resolved_owners=resolved_owners)
    warnings: list[GateIssue] = list(verdict.warnings)

    # R4：事实一致率仅 warning（ARCHITECTURE §9.2）。
    if fact_consistency is not None and fact_consistency < 0.98:
        warnings.append(
            GateIssue(
                gate="R4",
                severity="warning",
                code="FACT_CONSISTENCY_LOW",
                message=f"事实一致率 {fact_consistency:.2%} 低于 98%",
            )
        )

    verdict = ReviewVerdict(
        decision=verdict.decision,
        blocking=verdict.blocking,
        warnings=warnings,
        revision_round=revision_round,
        fingerprint=fingerprint(verdict.blocking),
    )
    return decide(
        verdict,
        kind=ctx.kind,
        revision_round=revision_round,
        previous_fingerprint=previous_fingerprint,
        previous_blocking_count=previous_blocking_count,
    )


def summarize_issues(issues: Sequence[GateIssue]) -> str:
    """生成 must_fix 摘要（不含正文，避免泄漏到日志/PR）。"""
    return "；".join(
        f"[{issue.gate}] {issue.message}" for issue in issues[:20]
    )
