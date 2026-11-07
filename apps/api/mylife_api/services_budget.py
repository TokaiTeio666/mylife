"""预算与配额（ARCHITECTURE §9.4；AC-66；SEC-23）。

run 级用量来自 `usage_events` 聚合（跨 attempt/阶段累计）；workspace 日/月
配额按 workspace 时区的自然日/自然月窗口计算。
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from mylife_agent.budget import BudgetDecision, BudgetExceeded, evaluate, usage_of
from mylife_contract.budget import Budget, BudgetUsage
from mylife_contract.constants import DEFAULT_LIMITS
from mylife_contract.errors import ErrorCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api.errors import AppError
from mylife_api.ratelimit import get_limiter

REVIEW_RATE_LIMIT = 10
REVIEW_RATE_WINDOW = 3600
REVIEW_DAILY_QUOTA = 50
REVIEW_DAILY_WINDOW = 86400


def workspace_window(now: datetime, timezone: str, scope: str) -> tuple[datetime, datetime]:
    """返回 `[start, end)` 的 UTC 时间；scope ∈ daily | monthly。"""
    try:
        zone = ZoneInfo(timezone)
    except Exception:  # noqa: BLE001 - 非法时区退回默认
        zone = ZoneInfo("Asia/Shanghai")
    local = now.astimezone(zone)
    if scope == "daily":
        start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
    else:
        start_local = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_local = (start_local + timedelta(days=32)).replace(day=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def workspace_limits(scope: str) -> Budget:
    monthly = DEFAULT_LIMITS["monthly_budget"]
    assert isinstance(monthly, dict)
    if scope == "workspace_monthly":
        return Budget(
            max_input_tokens=int(monthly["input_tokens"]),
            max_output_tokens=int(monthly["output_tokens"]),
        )
    return Budget(
        max_input_tokens=int(monthly["input_tokens"]) // 20,
        max_output_tokens=int(monthly["output_tokens"]) // 20,
    )


async def run_usage(session: AsyncSession, *, workspace_id: UUID, run_id: UUID) -> dict[str, Any]:
    result = await session.execute(
        text(
            "SELECT COALESCE(sum(prompt_tokens),0) AS input_tokens, "
            "       COALESCE(sum(completion_tokens),0) AS output_tokens, "
            "       COALESCE(sum(cost),0) AS cost_usd "
            "FROM usage_events WHERE workspace_id = :ws AND run_id = :run "
            "  AND kind IN ('chat','review')"
        ),
        {"ws": workspace_id, "run": run_id},
    )
    row = result.one()
    return {
        "input_tokens": int(row.input_tokens),
        "output_tokens": int(row.output_tokens),
        "cost_usd": float(row.cost_usd),
    }


async def workspace_usage(
    session: AsyncSession, *, workspace_id: UUID, start: datetime, end: datetime
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "SELECT COALESCE(sum(prompt_tokens),0) AS input_tokens, "
            "       COALESCE(sum(completion_tokens),0) AS output_tokens, "
            "       COALESCE(sum(cost),0) AS cost_usd "
            "FROM usage_events WHERE workspace_id = :ws "
            "  AND created_at >= :start AND created_at < :end "
            "  AND kind IN ('chat','review')"
        ),
        {"ws": workspace_id, "start": start, "end": end},
    )
    row = result.one()
    return {
        "input_tokens": int(row.input_tokens),
        "output_tokens": int(row.output_tokens),
        "cost_usd": float(row.cost_usd),
    }


async def run_budget_usage(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    budget: Budget,
) -> BudgetUsage:
    used = await run_usage(session, workspace_id=workspace_id, run_id=run_id)
    return usage_of(
        input_tokens=used["input_tokens"],
        output_tokens=used["output_tokens"],
        cost_usd=used["cost_usd"],
        budget=budget,
    )


async def check_workspace_quota(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    timezone: str,
    now: datetime | None = None,
) -> None:
    """workspace 日/月配额 100% 时拒绝新 run（AC-66）。"""
    now = now or datetime.now(UTC)
    for scope in ("workspace_monthly", "workspace_daily"):
        start, end = workspace_window(
            now, timezone, "monthly" if scope.endswith("monthly") else "daily"
        )
        used = await workspace_usage(session, workspace_id=workspace_id, start=start, end=end)
        limit = workspace_limits(scope)
        decision = evaluate(
            used_input=used["input_tokens"],
            used_output=used["output_tokens"],
            used_cost=used["cost_usd"],
            budget=limit,
            scope=scope,  # type: ignore[arg-type]
        )
        if decision.should_stop:
            raise AppError(
                ErrorCode.BUDGET_EXCEEDED,
                details={
                    "scope": scope,
                    "used": {
                        "input_tokens": used["input_tokens"],
                        "output_tokens": used["output_tokens"],
                    },
                    "limit": {
                        "input_tokens": limit.max_input_tokens,
                        "output_tokens": limit.max_output_tokens,
                    },
                    "resets_at": end.isoformat().replace("+00:00", "Z"),
                },
                headers={
                    "X-Quota-Limit": str(limit.max_input_tokens),
                    "X-Quota-Remaining": "0",
                    "X-Quota-Reset": str(int(end.timestamp())),
                },
            )


def _quota_headers(limit: Budget, end: datetime) -> dict[str, str]:
    return {
        "X-Quota-Limit": str(limit.max_input_tokens),
        "X-Quota-Remaining": "0",
        "X-Quota-Reset": str(int(end.timestamp())),
    }


async def enforce_review_quota(session: AsyncSession, *, workspace_id: UUID, user_id: UUID) -> None:
    """SEC-23：workspace 10/h + 50/d，拒绝写审计。"""
    limiter = get_limiter()
    per_hour = await limiter.hit(
        f"rl:review:ws:{workspace_id}", REVIEW_RATE_LIMIT, REVIEW_RATE_WINDOW
    )
    if not per_hour.allowed:
        await _audit_rejection(session, workspace_id, user_id, "RATE_LIMITED", REVIEW_RATE_LIMIT)
        raise AppError(
            ErrorCode.RATE_LIMITED,
            headers={"Retry-After": str(max(1, per_hour.reset_at - int(time.time())))},
        )
    per_day = await limiter.hit(
        f"quota:review:ws:{workspace_id}", REVIEW_DAILY_QUOTA, REVIEW_DAILY_WINDOW
    )
    if not per_day.allowed:
        await _audit_rejection(session, workspace_id, user_id, "QUOTA_EXCEEDED", REVIEW_DAILY_QUOTA)
        raise AppError(
            ErrorCode.QUOTA_EXCEEDED,
            details={"limit": REVIEW_DAILY_QUOTA, "window_seconds": REVIEW_DAILY_WINDOW},
        )


async def _audit_rejection(
    session: AsyncSession, workspace_id: UUID, user_id: UUID, reason: str, limit: int
) -> None:
    from mylife_api.repositories import insert_audit

    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="review.create_rejected",
        target=str(workspace_id),
        detail={"reason": reason, "limit": limit},
    )


def budget_from_options(kind: str, options: dict[str, Any] | None) -> Budget:
    from mylife_agent.budget import budget_for_kind

    default = budget_for_kind(kind)
    raw = (options or {}).get("budget") if options else None
    if not raw:
        return default
    if isinstance(raw, Budget):
        return raw
    if isinstance(raw, dict):
        payload = default.model_dump()
        payload.update({k: v for k, v in raw.items() if v is not None})
        return Budget(**payload)
    return default


def budget_payload(budget: Budget) -> dict[str, Any]:
    return json.loads(budget.model_dump_json())


def budget_exceeded_payload(decision: BudgetDecision) -> dict[str, Any]:
    return {
        "scope": decision.scope,
        "used": {
            "input_tokens": decision.usage.input_tokens,
            "output_tokens": decision.usage.output_tokens,
        },
        "ratio": decision.usage.ratio,
    }


__all__ = [
    "BudgetExceeded",
    "budget_from_options",
    "budget_payload",
    "budget_exceeded_payload",
    "check_workspace_quota",
    "enforce_review_quota",
    "run_budget_usage",
    "run_usage",
    "workspace_limits",
    "workspace_usage",
    "workspace_window",
]
