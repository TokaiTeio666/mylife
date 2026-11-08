"""仪表盘（API-CONTRACTS §3.8；AC-45…AC-47）。

只读、零 LLM；进程内 60s 缓存。所有数值可由 SQL 复算。
"""

from __future__ import annotations

import calendar
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from mylife_contract.review import ReviewSummary
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories

CACHE_TTL_SECONDS = 60
_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}


def clear_dashboard_cache() -> None:
    _cache.clear()


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Shanghai")


async def _timezone(session: AsyncSession, workspace_id: UUID) -> str:
    row = await repositories.get_settings(session, workspace_id) or {}
    return row.get("timezone") or "Asia/Shanghai"


def _summary(row: dict[str, Any]) -> ReviewSummary:
    return ReviewSummary(
        id=row["id"],
        kind=row["kind"],
        period_key=row["period_key"],
        title=row["title"],
        version=int(row["version"]),
        status=row["status"],
        generated_at=row["generated_at"],
        path=row.get("git_path"),
        degraded=bool(row.get("degraded")),
        delivery_status=row.get("delivery_status") or "none",
    )


async def get_latest(
    session: AsyncSession, *, workspace_id: UUID, timezone: str | None = None
) -> dict[str, Any]:
    key = ("latest", str(workspace_id), timezone)
    cached = _cache.get(key)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    tz = timezone or await _timezone(session, workspace_id)
    latest_reviews: dict[str, Any] = {}
    for kind in ("daily", "weekly", "yearly"):
        result = await session.execute(
            text(
                "SELECT id, kind, period_key, title, version, status, git_path, "
                "       degraded, generated_at, delivery_status FROM reviews "
                "WHERE workspace_id = :ws AND kind = :kind "
                "  AND status IN ('delivered','approved') "
                "ORDER BY period_key DESC, version DESC LIMIT 1"
            ),
            {"ws": workspace_id, "kind": kind},
        )
        row = result.first()
        latest_reviews[kind] = _summary(dict(row._mapping)) if row else None

    task_row = (
        await session.execute(
            text(
                "SELECT count(*) AS total, "
                "       count(*) FILTER (WHERE status = 'done') AS completed "
                "FROM tasks WHERE workspace_id = :ws"
            ),
            {"ws": workspace_id},
        )
    ).one()
    total = int(task_row.total)
    completed = int(task_row.completed)
    value = {
        "daily": latest_reviews["daily"],
        "weekly": latest_reviews["weekly"],
        "yearly": latest_reviews["yearly"],
        "generated_at": datetime.now(UTC),
        "completion_rate": round(completed / total, 4) if total else 0.0,
        "open_tasks": total - completed,
        "timezone": tz,
    }
    _cache[key] = (now + CACHE_TTL_SECONDS, value)
    return value


async def tasks(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    status: list[str] | None,
    limit: int,
    timezone: str | None = None,
) -> dict[str, Any]:
    tz = timezone or await _timezone(session, workspace_id)
    today_local = datetime.now(_tz(tz)).date()
    wanted = set(status or ["open", "carried"])

    result = await session.execute(
        text(
            "SELECT t.id, t.text, t.status, t.first_seen, t.last_seen, t.done_date, "
            "       t.source_path, t.line_number, "
            "       (SELECT count(*) FROM task_occurrences o "
            "         WHERE o.workspace_id = t.workspace_id AND o.task_id = t.id) "
            "         AS occurrence_count "
            "FROM tasks t WHERE t.workspace_id = :ws "
            "ORDER BY t.first_seen ASC, t.id ASC"
        ),
        {"ws": workspace_id},
    )
    rows = [dict(row._mapping) for row in result]
    items = []
    counts = {"open": 0, "carried": 0, "done_today": 0}
    for row in rows:
        derived = "open"
        if row["status"] == "done":
            derived = "done"
            if row["done_date"] == today_local:
                counts["done_today"] += 1
        elif row["first_seen"] is not None and row["first_seen"] < today_local:
            derived = "carried"
        if derived == "carried":
            counts["carried"] += 1
        elif derived == "open":
            counts["open"] += 1
        if derived not in wanted:
            continue
        days_open = 0
        if row["first_seen"] is not None:
            days_open = max(0, (today_local - row["first_seen"]).days)
        items.append(
            {
                "id": row["id"],
                "text": row["text"],
                "status": derived,
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "days_open": days_open,
                "done_date": row["done_date"],
                "source_path": row["source_path"],
                "line_number": row["line_number"],
                "occurrence_count": int(row["occurrence_count"]),
            }
        )
    return {
        "items": items[:limit],
        "next_cursor": None,
        "has_more": len(items) > limit,
        "counts": counts,
    }


async def calendar_view(
    session: AsyncSession, *, workspace_id: UUID, year: int, month: int | None
) -> dict[str, Any]:
    if month is not None:
        _, days_in_month = calendar.monthrange(year, month)
        start = date(year, month, 1).isoformat()
        end = date(year, month, days_in_month).isoformat()
        result = await session.execute(
            text(
                "SELECT period_key, status, degraded FROM reviews "
                "WHERE workspace_id = :ws AND kind = 'daily' "
                "  AND period_key BETWEEN :start AND :end"
            ),
            {"ws": workspace_id, "start": start, "end": end},
        )
        by_date = {row.period_key: row for row in result}
        # 该日仍未完成的任务数：first_seen <= 当日 且（未完成 或 done_date > 当日）。
        task_rows = await session.execute(
            text(
                "SELECT first_seen, done_date FROM tasks WHERE workspace_id = :ws "
                "AND first_seen <= :end "
                "AND (status <> 'done' OR done_date IS NULL OR done_date > :end)"
            ),
            {"ws": workspace_id, "end": end},
        )
        spans = [(row.first_seen, row.done_date) for row in task_rows]
        days = []
        for day in range(1, days_in_month + 1):
            current = date(year, month, day)
            key = current.isoformat()
            row = by_date.get(key)
            open_tasks = sum(
                1
                for first_seen, done_date in spans
                if first_seen is not None
                and first_seen <= current
                and (done_date is None or done_date > current)
            )
            days.append(
                {
                    "date": key,
                    "has_daily": row is not None,
                    "daily_status": row.status if row else None,
                    "degraded": bool(row.degraded) if row else False,
                    "open_tasks": open_tasks,
                }
            )
        return {"year": year, "months": [], "days": days}

    # 周报可能还没有 completion_rate 章节（仅 reviews 行），此时只标 has_weekly。
    result = await session.execute(
        text(
            "SELECT r.period_key, rs.structured_json FROM reviews r "
            "LEFT JOIN review_sections rs ON rs.workspace_id = r.workspace_id "
            "  AND rs.review_id = r.id AND rs.version = r.version "
            "  AND rs.section_key = 'completion_rate' "
            "WHERE r.workspace_id = :ws AND r.kind = 'weekly' "
            "  AND r.status IN ('delivered','approved') "
            "  AND r.period_key LIKE :prefix "
            "ORDER BY r.period_key DESC"
        ),
        {"ws": workspace_id, "prefix": f"{year}-W%"},
    )
    weekly: dict[int, dict[str, Any]] = {}
    for row in result:
        try:
            iso_week = int(str(row.period_key).split("-W")[1])
        except (ValueError, IndexError):
            continue
        structured = row.structured_json or {}
        rate = structured.get("rate") if isinstance(structured, dict) else None
        weekly.setdefault(
            iso_week,
            {
                "has_weekly": True,
                "weekly_period_key": row.period_key,
                "completion_rate": float(rate) if rate is not None else None,
            },
        )
    months = []
    for month in range(1, 13):
        hit = next(
            (
                value
                for week, value in weekly.items()
                if date.fromisocalendar(year, week, 1).month == month
            ),
            None,
        )
        months.append(
            {
                "month": month,
                "has_weekly": bool(hit),
                "weekly_period_key": (hit or {}).get("weekly_period_key"),
                "completion_rate": (hit or {}).get("completion_rate"),
            }
        )
    return {"year": year, "months": months, "days": []}


async def stats(
    session: AsyncSession, *, workspace_id: UUID, range_key: str
) -> dict[str, Any]:
    days = {"7d": 7, "30d": 30, "90d": 90, "year": 365}[range_key]
    start = datetime.now(UTC) - timedelta(days=days)
    task_row = (
        await session.execute(
            text(
                "SELECT count(*) AS total, "
                "       count(*) FILTER (WHERE status = 'done') AS completed, "
                "       count(*) FILTER (WHERE status = 'open' "
                "         AND first_seen < :start) AS carried "
                "FROM tasks WHERE workspace_id = :ws"
            ),
            {"ws": workspace_id, "start": start.date()},
        )
    ).one()
    usage_row = (
        await session.execute(
            text(
                "SELECT COALESCE(sum(prompt_tokens),0) AS prompt, "
                "       COALESCE(sum(completion_tokens),0) AS completion, "
                "       count(*) AS calls, "
                "       count(*) FILTER (WHERE degraded) AS degraded, "
                "       count(*) FILTER (WHERE kind = 'review') AS reviews "
                "FROM usage_events WHERE workspace_id = :ws AND created_at >= :start"
            ),
            {"ws": workspace_id, "start": start},
        )
    ).one()
    review_counts = (
        await session.execute(
            text(
                "SELECT kind, count(*) AS n FROM reviews WHERE workspace_id = :ws "
                "AND generated_at >= :start GROUP BY kind"
            ),
            {"ws": workspace_id, "start": start},
        )
    ).all()
    counts = {row.kind: int(row.n) for row in review_counts}
    total = int(task_row.total)
    completed = int(task_row.completed)
    calls = int(usage_row.calls)
    return {
        "range": range_key,
        "completion_rate": round(completed / total, 4) if total else 0.0,
        "tasks": {
            "completed": completed,
            "total": total,
            "carried_over": int(task_row.carried),
        },
        "reviews": {
            "daily": counts.get("daily", 0),
            "weekly": counts.get("weekly", 0),
            "yearly": counts.get("yearly", 0),
        },
        "no_evidence_ratio": round(
            (int(usage_row.reviews) - int(usage_row.degraded))
            / int(usage_row.reviews),
            4,
        )
        if int(usage_row.reviews)
        else 0.0,
        "degraded_rate": round(int(usage_row.degraded) / calls, 4) if calls else 0.0,
        "tokens": {
            "prompt": int(usage_row.prompt),
            "completion": int(usage_row.completion),
        },
    }
