"""Todo 台账与日期解析（AC-31/32/33/34）。"""

from __future__ import annotations

from datetime import date

import pytest
from mylife_agent.task_ledger import (
    extract_occurrences,
    normalize_task_text,
    task_key,
)
from mylife_worker.markdown import parse_document, resolve_occurred_on

from tests import helpers

# ---------------------------------------------------------------------------
# 纯解析
# ---------------------------------------------------------------------------


def test_checkbox_variants():
    lines = [
        "- [x] 写月报",
        "- [X] 大写勾选",
        "* [ ] 读论文",
        "+ [ ] 星号列表",
        "    - [ ] 缩进任务",
        "- [] 空标记不识别",
        "- [  ] 双空格不识别",
        "- [-] 横线不识别",
        "- [✓] 对勾不识别",
        "- [ x] 空格后 x 不识别",
    ]
    items = extract_occurrences(lines, body_start_line=1)
    assert [(item.line_number, item.checked, item.text) for item in items] == [
        (1, True, "写月报"),
        (2, True, "大写勾选"),
        (3, False, "读论文"),
        (4, False, "星号列表"),
        (5, False, "缩进任务"),
    ]


def test_fenced_code_is_ignored():
    lines = [
        "- [ ] 真实任务",
        "```md",
        "- [x] 代码块里的任务",
        "```",
        "~~~",
        "- [ ] 波浪线代码块",
        "~~~",
    ]
    items = extract_occurrences(lines, body_start_line=1)
    assert [item.text for item in items] == ["真实任务"]


def test_natural_language_produces_no_task():
    lines = ["今天完成了写月报", "我打算读论文", "完成了 3 件事"]
    assert extract_occurrences(lines, body_start_line=1) == []


def test_body_start_line_offset():
    text = "---\ndate: 2026-10-27\n---\n- [ ] 任务\n"
    parsed = parse_document("日记/2026/10.27.md", text)
    assert parsed.task_occurrences
    # frontmatter 占 3 行，正文第 1 行是文件第 4 行
    assert parsed.task_occurrences[0].line_number == 4


def test_normalize_task_text_and_key():
    # 连续空白（含 U+3000）折叠为一个半角空格，而不是删除
    assert normalize_task_text("  写　月报  ") == "写 月报"
    assert normalize_task_text("写　月报") == normalize_task_text("写 月报")
    assert normalize_task_text("写月报。") == normalize_task_text("写月报.")
    assert task_key("Report") == task_key("report")
    assert task_key("写月报") != task_key("读论文")


# ---------------------------------------------------------------------------
# 日期解析
# ---------------------------------------------------------------------------


def test_date_priority_frontmatter_over_path():
    resolved, warnings = resolve_occurred_on(
        {"date": "2026-10-28"}, "日记/2026/10.27.md"
    )
    assert resolved == date(2026, 10, 28)
    assert any(item["code"] == "DATE_SOURCE_CONFLICT" for item in warnings)


def test_short_filename_requires_year_directory():
    resolved, warnings = resolve_occurred_on({}, "日记/2026/10.27.md")
    assert resolved == date(2026, 10, 27)
    missing, warnings2 = resolve_occurred_on({}, "10.27.md")
    assert missing is None
    assert any(item["code"] == "DATE_YEAR_MISSING" for item in warnings2)


def test_directory_beats_short_filename():
    # 目录段必须是完整日期（YYYY-MM-DD / YYYY.MM.DD）才参与优先级
    resolved, warnings = resolve_occurred_on({}, "日记/2026-10-28/10.27.md")
    assert resolved == date(2026, 10, 28)
    assert any(item["code"] == "DATE_SOURCE_CONFLICT" for item in warnings)


def test_invalid_candidate_falls_back():
    resolved, warnings = resolve_occurred_on(
        {"date": "2026-13-40"}, "日记/2026/10.27.md"
    )
    assert resolved == date(2026, 10, 27)
    assert any(item["code"] == "DATE_INVALID" for item in warnings)


# ---------------------------------------------------------------------------
# 台账（DB）
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.db

FILES = {
    "日记/2026/10.25.md": "# 10-25\n- [ ] 写月报\n- [x] 读论文\n",
    "日记/2026/10.26.md": "# 10-26\n- [ ] 写月报\n",
    "日记/2026/10.27.md": "# 10-27\n- [x] 写月报\n- [ ] 交报告\n",
}


@pytest.mark.db
@pytest.mark.asyncio
async def test_cross_day_merge_and_occurrence_uniqueness(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="ledger1@example.com", files=FILES
    )

    rows = admin_conn.execute(
        "SELECT text, status, first_seen, last_seen, done_date, source_path, line_number "
        "FROM tasks WHERE workspace_id = %s AND text = '写月报'",
        (workspace_id,),
    ).fetchall()
    assert len(rows) == 1
    text, status, first_seen, last_seen, done_date, source_path, line_number = rows[0]
    assert status == "done"
    assert first_seen.isoformat() == "2026-10-25"
    assert last_seen.isoformat() == "2026-10-27"
    assert done_date.isoformat() == "2026-10-27"
    assert source_path == "日记/2026/10.27.md"
    assert line_number == 2

    duplicates = admin_conn.execute(
        "SELECT count(*) FROM ("
        "  SELECT workspace_id, document_id, occurrence_date, line_number"
        "  FROM task_occurrences WHERE workspace_id = %s"
        "  GROUP BY 1,2,3,4 HAVING count(*) > 1"
        ") dup",
        (workspace_id,),
    ).fetchone()[0]
    assert duplicates == 0


@pytest.mark.db
@pytest.mark.asyncio
async def test_occurrence_not_duplicated_on_reimport(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="ledger2@example.com", files=FILES
    )
    before = admin_conn.execute(
        "SELECT count(*) FROM task_occurrences WHERE workspace_id = %s", (workspace_id,)
    ).fetchone()[0]
    assert before > 0

    upload = await helpers.upload_zip(client, data=helpers.make_zip(FILES))
    assert upload.status_code == 202, upload.text
    await helpers.run_ingest_job(upload.json()["job"]["id"], workspace_id)

    after = admin_conn.execute(
        "SELECT count(*) FROM task_occurrences WHERE workspace_id = %s", (workspace_id,)
    ).fetchone()[0]
    assert after == before


@pytest.mark.db
@pytest.mark.asyncio
async def test_deleting_document_removes_occurrence_and_orphan_task(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="ledger3@example.com", files=FILES
    )
    remaining = {k: v for k, v in FILES.items() if "10.27" not in k}
    upload = await helpers.upload_zip(client, data=helpers.make_zip(remaining))
    await helpers.run_ingest_job(upload.json()["job"]["id"], workspace_id)

    assert (
        admin_conn.execute(
            "SELECT count(*) FROM documents WHERE workspace_id = %s AND path = %s",
            (workspace_id, "日记/2026/10.27.md"),
        ).fetchone()[0]
        == 0
    )
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM task_occurrences o JOIN documents d ON d.id = o.document_id "
            "WHERE o.workspace_id = %s AND d.path = %s",
            (workspace_id, "日记/2026/10.27.md"),
        ).fetchone()[0]
        == 0
    )
    # 只在 10-27 出现的「交报告」应成为孤儿并被清理
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM tasks WHERE workspace_id = %s AND text = '交报告'",
            (workspace_id,),
        ).fetchone()[0]
        == 0
    )


@pytest.mark.db
@pytest.mark.asyncio
async def test_carried_derived_not_persisted(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="ledger4@example.com", files=FILES
    )
    statuses = {
        row[0]
        for row in admin_conn.execute(
            "SELECT DISTINCT status FROM tasks WHERE workspace_id = %s", (workspace_id,)
        ).fetchall()
    }
    assert statuses <= {"open", "done"}
    carried = admin_conn.execute(
        "SELECT text FROM tasks WHERE workspace_id = %s AND status = 'open' "
        "AND first_seen < DATE '2026-10-28'",
        (workspace_id,),
    ).fetchall()
    assert [row[0] for row in carried] == ["交报告"]
    # 10-27 当天首次出现，不属于「以往顺延」
    same_day = admin_conn.execute(
        "SELECT text FROM tasks WHERE workspace_id = %s AND status = 'open' "
        "AND first_seen < DATE '2026-10-27'",
        (workspace_id,),
    ).fetchall()
    assert same_day == []


@pytest.mark.db
@pytest.mark.asyncio
async def test_date_conflict_warning_persisted(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client,
        admin_conn,
        email="ledger5@example.com",
        files={
            "日记/2026/10.27.md": "---\ndate: 2026-10-28\n---\n- [ ] 冲突日期\n",
        },
    )
    row = admin_conn.execute(
        "SELECT occurred_on, parse_warnings FROM documents WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()
    assert row[0].isoformat() == "2026-10-28"
    assert any(item.get("code") == "DATE_SOURCE_CONFLICT" for item in row[1])


@pytest.mark.db
@pytest.mark.asyncio
async def test_ingest_path_makes_zero_llm_calls(client, admin_conn, monkeypatch):
    from mylife_agent import llm

    calls = {"count": 0}
    original = llm.MockLLMProvider.stream

    async def counting(self, request):
        calls["count"] += 1
        async for delta in original(self, request):
            yield delta

    monkeypatch.setattr(llm.MockLLMProvider, "stream", counting)

    await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="ledger6@example.com", files=FILES
    )
    assert calls["count"] == 0
