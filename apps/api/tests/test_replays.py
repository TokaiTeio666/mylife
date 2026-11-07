"""AC-60：结构化日志字段与 run_events 回放。"""

from __future__ import annotations

import json
import logging
import uuid

import pytest

from mylife_api.logging_setup import (
    JsonFormatter,
    bind_run_context,
    clear_run_context,
)
from mylife_api.services_chat import wait_for_run
from tests import helpers
from tests.helpers import API

DOCS = {"notes/redis.md": "---\ndate: 2026-10-27\n---\n# Redis\n缓存穿透。\n"}


def test_log_formatter_emits_required_fields():
    formatter = JsonFormatter("mylife-api")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="chat.done", args=(), exc_info=None,
    )
    bind_run_context(trace_id="req_1", workspace_id="ws-1", run_id="run-1")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        clear_run_context()
    assert payload["service"] == "mylife-api"
    assert payload["level"] == "INFO"
    assert payload["event"] == "chat.done"
    assert payload["trace_id"] == "req_1"
    assert payload["workspace_id"] == "ws-1"
    assert payload["run_id"] == "run-1"
    assert payload["ts"].endswith("Z")


def test_log_formatter_drops_content_fields():
    formatter = JsonFormatter("mylife-api")
    record = logging.LogRecord(
        name="test", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="leak", args=(), exc_info=None,
    )
    record.content = "笔记正文"
    record.quote = "证据片段"
    record.api_key = "sk-live"
    record.path = "notes/a.md"
    payload = json.loads(formatter.format(record))
    assert "content" not in payload
    assert "quote" not in payload
    assert "api_key" not in payload
    assert payload["path"] == "notes/a.md"


@pytest.mark.db
@pytest.mark.asyncio
async def test_chat_run_events_replay_matches_db(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="replay1@example.com", files=DOCS
    )
    token = await helpers.csrf(client)
    conversation = await client.post(
        f"{API}/conversations", json={"title": "回放"}, headers=helpers.csrf_headers(token)
    )
    conversation_id = conversation.json()["id"]
    turn = await helpers.start_turn(client, conversation_id, message="缓存")
    run_id = turn.json()["run_id"]
    await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(run_id))

    rows = admin_conn.execute(
        "SELECT from_state, to_state FROM run_events WHERE run_id = %s "
        "AND event = 'run.state' ORDER BY event_id",
        (run_id,),
    ).fetchall()
    assert rows
    assert rows[0][0] in (None, "created")
    for index in range(1, len(rows)):
        assert rows[index][0] == rows[index - 1][1], (index, rows)
    db_state = admin_conn.execute(
        "SELECT state FROM agent_runs WHERE id = %s", (run_id,)
    ).fetchone()[0]
    assert rows[-1][1] == db_state
    assert db_state in {"succeeded", "failed", "cancelled"}


@pytest.mark.db
@pytest.mark.asyncio
async def test_review_run_events_replay(client, admin_conn):
    from mylife_api.services_review import wait_for_run as wait_review

    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client,
        admin_conn,
        email="replay2@example.com",
        files={"日记/2026/10.27.md": "---\ndate: 2026-10-27\n---\n# 10-27\n内容\n"},
    )
    token = await helpers.csrf(client)
    created = await client.post(
        f"{API}/reviews",
        json={"kind": "daily", "period_key": "2026-10-27"},
        headers=helpers.csrf_headers(token),
    )
    run_id = created.json()["run_id"]
    await wait_review(uuid.UUID(workspace_id), uuid.UUID(run_id))

    rows = admin_conn.execute(
        "SELECT from_state, to_state, payload FROM run_events WHERE run_id = %s "
        "AND event = 'run.state' ORDER BY event_id",
        (run_id,),
    ).fetchall()
    assert [row[0] for row in rows] == [
        "created",
        "snapshotting",
        "planning",
        "retrieving",
        "drafting",
        "reviewing",
        "delivering",
    ]
    assert rows[-1][1] == "succeeded"
    for row in rows:
        assert row[2]["reason"]
        assert row[2]["event"] == "state.transition"

    # stream_epoch 已写入
    epochs = admin_conn.execute(
        "SELECT DISTINCT stream_epoch FROM run_events WHERE run_id = %s", (run_id,)
    ).fetchall()
    assert all(value[0] is not None for value in epochs)
