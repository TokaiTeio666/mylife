"""调度时间计算（ARCHITECTURE §11/§16.6；AC-43/AC-51）。

纯函数，无数据库与副作用：
- daily：本地日期 + 时间；
- monthly：每月 1 日 + 时间（生成上一个月）；
- yearly：固定 1 月 1 日 + 时间；
- DST：不存在的本地时刻顺延，歧义时刻取较早 offset（fold=0）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DAILY = "daily"
MONTHLY = "monthly"
YEARLY = "yearly"


def zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except Exception as exc:  # noqa: BLE001 - 统一转成值错误
        raise ValueError(f"invalid timezone: {tz}") from exc


def localize(day: date, at: time, tz: str) -> datetime:
    """把本地 `date + time` 转成 UTC；处理 DST 不存在/歧义时刻。"""
    tzinfo = zone(tz)
    candidate = datetime.combine(day, at, tzinfo=tzinfo)
    roundtrip = candidate.astimezone(UTC).astimezone(tzinfo)
    if roundtrip.replace(tzinfo=None) != candidate.replace(tzinfo=None):
        # 不存在的本地时刻：按偏移差顺延到下一个有效时刻。
        candidate = candidate + (
            roundtrip.replace(tzinfo=None) - candidate.replace(tzinfo=None)
        )
        candidate = candidate.replace(tzinfo=tzinfo)
    return candidate.astimezone(UTC)


def next_run_at(kind: str, at: time, tz: str, after: datetime) -> datetime:
    """返回严格晚于 `after` 的下一次运行时刻（UTC）。"""
    after = after if after.tzinfo else after.replace(tzinfo=UTC)
    local_date = after.astimezone(zone(tz)).date()

    if kind == DAILY:
        day = local_date
        for _ in range(4):
            candidate = localize(day, at, tz)
            if candidate > after:
                return candidate
            day += timedelta(days=1)
    elif kind == MONTHLY:
        day = local_date.replace(day=1)
        for _ in range(4):
            candidate = localize(day, at, tz)
            if candidate > after:
                return candidate
            # 下一个月的 1 日。
            day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    elif kind == YEARLY:
        for year in (local_date.year, local_date.year + 1):
            candidate = localize(date(year, 1, 1), at, tz)
            if candidate > after:
                return candidate
    else:  # pragma: no cover - 由 CHECK 约束保证
        raise ValueError(f"unknown schedule kind: {kind}")
    raise RuntimeError("无法计算 next_run_at")  # pragma: no cover


def due_period(kind: str, run_date: date) -> str:
    """运行日 → 要生成的周期键。

    daily 生成 D-1；monthly（每月 1 日运行）生成上一个月；yearly（1/1 运行）
    生成上一年。
    """
    if kind == DAILY:
        return (run_date - timedelta(days=1)).isoformat()
    if kind == MONTHLY:
        previous = run_date.replace(day=1) - timedelta(days=1)
        return f"{previous.year}-{previous.month:02d}"
    return str(run_date.year - 1)


def missed_occurrences(
    *,
    kind: str,
    at: time,
    tz: str,
    since: datetime,
    now: datetime,
    catchup_days: int,
    due_at: datetime | None = None,
) -> tuple[list[datetime], bool]:
    """返回窗口内最近 `catchup_days` 个应运行时刻（oldest→newest）与超窗标志。

    `due_at` 是调度行里已经到期但尚未处理的时刻；它本身必须计入结果
    （`next_run_at` 只返回严格晚于 `since` 的时刻，会漏掉边界）。
    """
    since = since if since.tzinfo else since.replace(tzinfo=UTC)
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    occurrences: list[datetime] = []
    cursor = since
    if due_at is not None:
        due_at = due_at if due_at.tzinfo else due_at.replace(tzinfo=UTC)
        if due_at <= now:
            occurrences.append(due_at)
            cursor = max(cursor, due_at)
    # 防御：即使停机很久也最多迭代 400 次（每天一次约一年）。
    for _ in range(400):
        candidate = next_run_at(kind, at, tz, cursor)
        if candidate > now:
            break
        occurrences.append(candidate)
        cursor = candidate
    if not occurrences:
        return [], False

    local_now = now.astimezone(zone(tz))
    threshold = local_now - timedelta(days=max(0, catchup_days))
    kept = [
        item for item in occurrences if item.astimezone(zone(tz)) >= threshold
    ]
    exceeded = len(kept) != len(occurrences)
    if kind != DAILY:
        kept = kept[-max(1, catchup_days):] if catchup_days else []
    return kept, exceeded


__all__ = [
    "DAILY",
    "MONTHLY",
    "YEARLY",
    "due_period",
    "localize",
    "missed_occurrences",
    "next_run_at",
    "zone",
]
