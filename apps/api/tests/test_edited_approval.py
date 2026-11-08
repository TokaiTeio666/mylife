"""编辑重评审的确定性门禁回归（PHASE5-SPEC D31）。

Phase 4 的两个缺陷在这里固化为用例：
1. `repo_review.list_entries` 漏选 `source_date`，导致 G4 把每个错误/方法论条目
   判为不可追溯，带编辑的审批必然 422；
2. `_edited_sections` 不还原 `sentinel_used`，哨兵章节在重评审里被当成普通章节，
   同时触发 G2（structured 为空）与 G7（有证据章节出现哨兵文案）。
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from tests import helpers

pytestmark = pytest.mark.db

API = helpers.API

DAILY_SECTIONS = (
    ("today_tasks", 1, "今日待办"),
    ("learning_summary", 2, "学习内容摘要"),
    ("journal_events", 3, "日记事件摘要"),
    ("mistakes", 4, "做错的事"),
    ("methods", 5, "正确方法论"),
    ("tomorrow_actions", 6, "明日行动"),
    ("one_line_summary", 7, "一句话总结"),
)


def _seed_review(conn, workspace_id: str) -> tuple[str, str]:
    """插入一条已定稿的 daily 复盘：一个可追溯错误条目 + 两个哨兵章节。"""
    from mylife_api.ids import uuid7

    review_id = uuid7()
    run_id = uuid7()
    conn.execute(
        "INSERT INTO reviews (id, workspace_id, kind, period_key, title, content, version) "
        "VALUES (%s, %s, 'daily', '2026-10-27', '日报', '# 日报', 1)",
        (review_id, workspace_id),
    )
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, workspace_id, kind, period_key, state, idem_key, review_id) "
        "VALUES (%s, %s, 'daily', '2026-10-27', 'human_gate', %s, %s)",
        (run_id, workspace_id, f"test-{run_id}", review_id),
    )
    for key, ordinal, title in DAILY_SECTIONS:
        if key == "mistakes":
            content = "## 做错的事\n\n- 事件：缓存穿透 | 根因：未加布隆过滤器"
        elif key == "tomorrow_actions":
            content = "明日行动：无（未找到可执行依据）"
        elif key == "one_line_summary":
            content = "一句话总结：本日无可总结内容"
        else:
            content = f"## {title}\n\n本日记录。"
        conn.execute(
            "INSERT INTO review_sections "
            "(id, workspace_id, review_id, version, section_key, ordinal, content_md, "
            " structured_json) VALUES (%s, %s, %s, 1, %s, %s, %s, CAST(%s AS jsonb))",
            (uuid7(), workspace_id, review_id, key, ordinal, content, json.dumps({})),
        )
    conn.execute(
        "INSERT INTO review_entries "
        "(id, workspace_id, review_id, version, section_key, item_type, canonical_key, "
        " fields_json, source_path, line_start, line_end, source_date) "
        "VALUES (%s, %s, %s, 1, 'mistakes', 'error', 'k-1', CAST(%s AS jsonb), "
        "        '日记/2026/10.27.md', 5, 5, '2026-10-27')",
        (uuid7(), workspace_id, review_id, json.dumps({"event": "缓存穿透"})),
    )
    return str(review_id), str(run_id)


@pytest.mark.asyncio
async def test_list_entries_returns_source_date(admin_conn, db_session):
    """G4 依赖 source_date；缺列会让所有错误条目不可追溯。"""
    from mylife_api import repo_review
    from mylife_api.db import set_rls_context

    user_id = helpers.insert_user(admin_conn, email="entry@example.com")
    workspace_id = helpers.insert_workspace(admin_conn, owner_id=user_id)
    review_id, _ = _seed_review(admin_conn, workspace_id)
    await set_rls_context(db_session, UUID(workspace_id), UUID(user_id))

    rows = await repo_review.list_entries(
        db_session, workspace_id=UUID(workspace_id), review_id=UUID(review_id), version=1
    )
    assert rows, "应至少有一个条目"
    entry = next(row for row in rows if row["item_type"] == "error")
    assert entry["source_date"] is not None
    assert str(entry["source_date"]) == "2026-10-27"
    assert entry["source_path"] == "日记/2026/10.27.md"
    assert entry["line_start"] == 5 and entry["line_end"] == 5


@pytest.mark.asyncio
async def test_edited_sections_restore_sentinel_flag(admin_conn, db_session):
    """哨兵章节必须带 sentinel_used，否则 G2/G7 误判。"""
    from mylife_api import services_review
    from mylife_api.db import set_rls_context

    user_id = helpers.insert_user(admin_conn, email="sentinel@example.com")
    workspace_id = helpers.insert_workspace(admin_conn, owner_id=user_id)
    review_id, _ = _seed_review(admin_conn, workspace_id)
    await set_rls_context(db_session, UUID(workspace_id), UUID(user_id))

    edited = "\n".join(
        f"## {title}\n\n{content}"
        for title, content in [
            ("今日待办", "本日记录。"),
            ("学习内容摘要", "本日记录。"),
            ("日记事件摘要", "本日记录。"),
            ("做错的事", "- 事件：缓存穿透 | 根因：未加布隆过滤器"),
            ("正确方法论", "本日记录。"),
            ("明日行动", "明日行动：无（未找到可执行依据）"),
            ("一句话总结", "一句话总结：本日无可总结内容"),
        ]
    )
    sections = await services_review._edited_sections(
        db_session,
        workspace_id=UUID(workspace_id),
        review_id=UUID(review_id),
        version=1,
        kind="daily",
        edited_content_md=edited,
    )
    by_key = {section["section_key"]: section for section in sections}
    assert by_key["tomorrow_actions"]["sentinel_used"] is True
    assert by_key["one_line_summary"]["sentinel_used"] is True
    # 有正文的章节不能被误标为哨兵
    assert by_key["today_tasks"]["sentinel_used"] is False
    assert by_key["mistakes"]["sentinel_used"] is False
