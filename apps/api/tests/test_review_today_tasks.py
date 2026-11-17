"""今日待办三态装配（已完成 / 未完成 / 暂停中）。"""

from __future__ import annotations

from datetime import date

from mylife_api.review_composer import compose_daily
from mylife_api.review_evidence import EvidenceCollector

PERIOD = date(2026, 11, 3)
PATH = "日记/2026/11.03.md"


def _draft():
    collector = EvidenceCollector(parser_version="mylife-parser-1")
    evidence: dict[str, str] = {}
    for key, text, line in (
        ("completed", "写月报", 5),
        ("open", "读论文", 6),
        ("paused", "暂停实验", 7),
    ):
        evidence[key] = collector.add(
            "today_tasks",
            source_type="task",
            quote=text,
            source_path=PATH,
            source_date=PERIOD,
            line_range=[line, line],
        ) or ""

    period_tasks = {
        key: [
            {
                "text": text,
                "source_date": PERIOD,
                "source_path": PATH,
                "line_range": [line, line],
                "evidence_id": evidence[key],
            }
        ]
        for key, text, line in (
            ("completed", "写月报", 5),
            ("open", "读论文", 6),
            ("paused", "暂停实验", 7),
        )
    }
    carried = [
        {
            "id": "task-1",
            "text": "整理旧笔记",
            "first_seen": date(2026, 10, 25),
            "source_path": "日记/2026/10.25.md",
            "line_number": 3,
        }
    ]
    return compose_daily(
        period_key=PERIOD,
        collector=collector,
        period_tasks=period_tasks,
        carried_tasks=carried,
        journal_docs=[],
        learning_docs=[],
        entries=[],
    )


def test_today_tasks_has_three_states():
    draft = _draft()
    section = next(item for item in draft.sections if item.section_key == "today_tasks")

    assert "### 已完成" in section.content_md
    assert "### 未完成" in section.content_md
    assert "### 暂停中" in section.content_md
    assert "### 以往顺延" not in section.content_md

    assert [item["text"] for item in section.structured["completed"]] == ["写月报"]
    assert [item["text"] for item in section.structured["open"]] == ["读论文"]
    assert [item["text"] for item in section.structured["paused"]] == ["暂停实验"]
    assert [item["text"] for item in section.structured["carried_over"]] == ["整理旧笔记"]

    # 以往顺延保留在「未完成」组内，不丢事实。
    assert "整理旧笔记（自 2026-10-25 顺延）" in section.content_md
