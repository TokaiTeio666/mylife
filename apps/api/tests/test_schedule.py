"""调度时间计算（AC-43）：时区、DST、周期与补算窗口。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from mylife_api.schedule import (
    due_period,
    localize,
    missed_occurrences,
    next_run_at,
)


def test_localize_shanghai():
    value = localize(date(2026, 10, 27), time(1, 0), "Asia/Shanghai")
    assert value == datetime(2026, 10, 26, 17, 0, tzinfo=UTC)


def test_next_run_daily_timezone_change():
    after = datetime(2026, 10, 27, 0, 0, tzinfo=UTC)  # 上海 08:00
    assert next_run_at("daily", time(1, 0), "Asia/Shanghai", after) == datetime(
        2026, 10, 27, 17, 0, tzinfo=UTC
    )
    # 纽约 01:00 EDT = 05:00Z；当地时间 08:00 之后 → 次日 01:00 EDT
    assert next_run_at("daily", time(1, 0), "America/New_York", after) == datetime(
        2026, 10, 27, 5, 0, tzinfo=UTC
    )


def test_next_run_monthly_is_first_day():
    after = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)  # 上海 10-23 20:00
    value = next_run_at("monthly", time(1, 30), "Asia/Shanghai", after)
    assert value == datetime(2026, 10, 31, 17, 30, tzinfo=UTC)  # 11-01 01:30 CST


def test_next_run_yearly_is_jan_first():
    after = datetime(2026, 6, 1, tzinfo=UTC)
    value = next_run_at("yearly", time(2, 0), "Asia/Shanghai", after)
    assert value == datetime(2026, 12, 31, 18, 0, tzinfo=UTC)  # 2027-01-01 02:00 CST


def test_dst_nonexistent_time_shifts_forward():
    # 2026-03-08 02:30 America/New_York 不存在 → 顺延到 03:30 EDT = 07:30Z
    value = localize(date(2026, 3, 8), time(2, 30), "America/New_York")
    assert value == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)


def test_dst_ambiguous_time_takes_earlier_offset():
    # 2026-11-01 01:30 出现两次；较早 offset 为 EDT(UTC-4) = 05:30Z
    value = localize(date(2026, 11, 1), time(1, 30), "America/New_York")
    assert value == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_due_periods():
    assert due_period("daily", date(2026, 10, 28)) == "2026-10-27"
    assert due_period("monthly", date(2026, 11, 2)) == "2026-10"
    assert due_period("monthly", date(2027, 1, 1)) == "2026-12"
    assert due_period("yearly", date(2027, 1, 1)) == "2026"


def test_catchup_window_truncates():
    since = datetime(2026, 10, 10, 17, 0, tzinfo=UTC)
    now = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    occurrences, exceeded = missed_occurrences(
        kind="daily",
        at=time(1, 0),
        tz="Asia/Shanghai",
        since=since,
        now=now,
        catchup_days=7,
    )
    assert exceeded is True
    assert len(occurrences) == 7
