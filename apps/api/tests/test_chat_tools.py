"""ToolGateway 契约与六工具（AC-25；SEC-09）。"""

from __future__ import annotations

import uuid

import pytest
from mylife_contract.tools import (
    CreateReviewArgs,
    GetReviewArgs,
    GetStatsArgs,
    ListTasksArgs,
    ReadNoteArgs,
    SearchNotesArgs,
)

from mylife_api.db import set_rls_context
from mylife_api.tools import ToolContext, execute_tool
from tests import helpers

pytestmark = pytest.mark.db

DOCS = {
    "notes/redis.md": "---\ntags: [redis]\ndate: 2026-10-27\n---\n# Redis\n缓存穿透与布隆过滤器。",
    "notes/mysql.md": "---\ntags: [mysql]\ndate: 2026-10-20\n---\n# MySQL\n索引与事务。",
}


async def _ctx(session, workspace_id: str, user_id: str) -> ToolContext:
    await set_rls_context(session, workspace_id, user_id)
    return ToolContext(
        workspace_id=uuid.UUID(workspace_id),
        user_id=uuid.UUID(user_id),
        run_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_six_tools_return_structured_results(client, admin_conn, db_session):
    workspace_id, user_id = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="tools@example.com", files=DOCS
    )
    ctx = await _ctx(db_session, workspace_id, user_id)

    search = await execute_tool(
        db_session, ctx, "search_notes", {"query": "缓存穿透", "top_k": 5}
    )
    assert search.status == "ok", search.summary
    assert search.result.items
    assert "<mark>" not in search.result.items[0].snippet

    document_id = str(search.result.items[0].document_id)
    read = await execute_tool(db_session, ctx, "read_note", {"document_id": document_id})
    assert read.status == "ok"
    assert read.result.content.startswith("<<<EVIDENCE")
    assert "缓存" in read.result.content

    admin_conn.execute(
        "INSERT INTO tasks (id, workspace_id, key, text, status, first_seen, last_seen, "
        "done_date, source_path, line_number) VALUES "
        "(gen_random_uuid(), %s, 'k1', '写月报', 'done', DATE '2026-10-25', DATE '2026-10-27', "
        " DATE '2026-10-27', 'notes/a.md', 3), "
        "(gen_random_uuid(), %s, 'k2', '读论文', 'open', DATE '2026-10-26', DATE '2026-10-27', "
        " NULL, 'notes/b.md', 4)",
        (workspace_id, workspace_id),
    )
    tasks = await execute_tool(db_session, ctx, "list_tasks", {"limit": 10})
    assert tasks.status == "ok"
    assert tasks.result.counts.get("done") == 1
    assert tasks.result.counts.get("open") == 1
    assert tasks.result.ledger_recalc["total"] == 2

    admin_conn.execute(
        "INSERT INTO reviews (id, workspace_id, kind, period_key, title, content) "
        "VALUES (gen_random_uuid(), %s, 'daily', '2026-10-27', '日报', '内容')",
        (workspace_id,),
    )
    review = await execute_tool(
        db_session, ctx, "get_review", {"kind": "daily", "period_key": "2026-10-27"}
    )
    assert review.status == "ok"
    assert review.result.title == "日报"

    stats = await execute_tool(
        db_session,
        ctx,
        "get_stats",
        {"metric": "completion_rate", "from": "2026-10-01", "to": "2026-10-31"},
    )
    assert stats.status == "ok"
    assert stats.result.metric == "completion_rate"
    assert stats.result.value["total"] == 2

    before = helpers.row_count(
        admin_conn, "SELECT count(*) FROM reviews WHERE workspace_id = %s", (workspace_id,)
    )
    preview = await execute_tool(
        db_session,
        ctx,
        "create_review",
        {"kind": "daily", "period_key": "2026-10-27"},
    )
    assert preview.status == "ok"
    assert preview.result.confirmation_token.startswith("v1.")
    assert preview.result.preview.path.endswith("2026-10-27.md")
    after = helpers.row_count(
        admin_conn, "SELECT count(*) FROM reviews WHERE workspace_id = %s", (workspace_id,)
    )
    assert after == before  # preview 零副作用


@pytest.mark.asyncio
async def test_tool_args_reject_workspace_id(client, admin_conn, db_session):
    workspace_id, user_id = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="tools-forbid@example.com", files=DOCS
    )
    ctx = await _ctx(db_session, workspace_id, user_id)
    result = await execute_tool(
        db_session,
        ctx,
        "search_notes",
        {"query": "缓存", "workspace_id": str(uuid.uuid4())},
    )
    assert result.status == "error"
    assert result.error_code == "TOOL_ARGS_INVALID"


def test_tool_arg_models_forbid_workspace_id():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SearchNotesArgs.model_validate(
            {"query": "x", "workspace_id": "00000000-0000-0000-0000-000000000000"}
        )
    with pytest.raises(ValidationError):
        ReadNoteArgs.model_validate({"path": "a.md", "workspace_id": "y"})
    with pytest.raises(ValidationError):
        ListTasksArgs.model_validate({"workspace_id": "y"})
    with pytest.raises(ValidationError):
        GetReviewArgs.model_validate(
            {"kind": "daily", "period_key": "2026-10-27", "workspace_id": "y"}
        )
    with pytest.raises(ValidationError):
        GetStatsArgs.model_validate(
            {"metric": "task_counts", "from": "2026-01-01", "to": "2026-12-31", "workspace_id": "y"}
        )
    with pytest.raises(ValidationError):
        CreateReviewArgs.model_validate(
            {"kind": "daily", "period_key": "2026-10-27", "workspace_id": "y"}
        )
