"""周/年 rollup 派生（AC-37/AC-38）。"""

from __future__ import annotations

from mylife_api.review_runner import (
    _learning_map,
    _method_landing,
    _method_system,
    _recurring,
    _timeline,
    _top_mistakes,
)


def test_recurring_errors_require_two_occurrences_and_sort_deterministically():
    entries = [
        {"item_type": "error", "canonical_key": "k-b", "source_date": "2026-10-20"},
        {"item_type": "error", "canonical_key": "k-a", "source_date": "2026-10-21"},
        {"item_type": "error", "canonical_key": "k-a", "source_date": "2026-10-22"},
        {"item_type": "error", "canonical_key": "k-c", "source_date": "2026-10-23"},
        {"item_type": "method", "canonical_key": "k-d", "source_date": "2026-10-23"},
    ]
    rows = _recurring(entries)
    assert [row["canonical_key"] for row in rows] == ["k-a"]
    assert rows[0]["occurrences"] == 2
    assert rows[0]["first_seen"] == "2026-10-21"
    assert rows[0]["last_seen"] == "2026-10-22"


def test_top_mistakes_frequency_desc_then_key_asc_and_cap_ten():
    entries = []
    for index in range(12):
        key = f"k{index:02d}"
        occurrences = 3 if index < 3 else 1
        for _ in range(occurrences):
            entries.append(
                {
                    "item_type": "error",
                    "canonical_key": key,
                    "fields": {"event": f"事件 {index}"},
                    "source_path": "notes/a.md",
                }
            )
    rows = _top_mistakes(entries)
    assert len(rows) == 10
    assert [row["canonical_key"] for row in rows[:3]] == ["k00", "k01", "k02"]
    # 并列时 canonical_key 升序
    ties = [row["canonical_key"] for row in rows[3:]]
    assert ties == sorted(ties)


def test_learning_map_reads_weekly_rollup_only():
    sections = [
        {
            "section_key": "method_landing",
            "period_key": "2026-W43",
            "structured": {
                "items": [
                    {
                        "method": "先写测试",
                        "action_count": 2,
                        "verification_status": "verified",
                    }
                ]
            },
        },
        {
            "section_key": "learning_summary",
            "period_key": "2026-10-27",
            "structured": {"topic": "缓存", "concept_or_method": "布隆过滤器", "output": "笔记"},
        },
    ]
    mapped = _learning_map(sections)
    assert mapped["topics"] == ["先写测试", "缓存"]
    assert mapped["concepts"] == ["verified", "布隆过滤器"]
    assert mapped["outputs"] == ["笔记"]
    assert mapped["related_weeks"] == ["2026-W43"]


def test_timeline_prefers_journal_and_falls_back_to_weekly_actions():
    sections = [
        {
            "section_key": "journal_events",
            "structured": {"items": [{"event": "复盘", "result": "完成"}]},
            "period_key": "2026-10-27",
        },
        {
            "section_key": "next_week_actions",
            "structured": {"items": [{"text": "补测试", "verification": "CI 全绿"}]},
            "period_key": "2026-W43",
        },
    ]
    rows = _timeline(sections)
    assert {"date": "2026-10-27", "event": "复盘", "result": "完成"} in rows
    assert {"date": "2026-W43", "event": "补测试", "result": "CI 全绿"} in rows


def test_method_landing_and_system_derive_from_entries():
    entries = [
        {
            "item_type": "method",
            "canonical_key": "m1",
            "fields": {
                "name": "先写测试",
                "source": "实践",
                "applicable_scenario": "接口改造",
                "verification_criteria": "CI 全绿",
            },
        }
    ]
    landing = _method_landing(entries)
    assert landing[0]["method"] == "先写测试"
    assert landing[0]["verification_status"] == "pending"

    system = _method_system(entries)
    assert system[0]["category"] == "实践"
    assert system[0]["verification"] == "CI 全绿"
