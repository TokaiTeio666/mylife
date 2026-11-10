"""复盘准入与生成（AC-25/36/42/68）。"""

from __future__ import annotations

import uuid

import pytest

from mylife_api.services_review import wait_for_run
from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

DAILY_FILES = {
    "日记/2026/10.27.md": "---\ndate: 2026-10-27\n---\n# 10-27\n今天整理了缓存方案。\n",
    "日记/2026/10.26.md": "---\ndate: 2026-10-26\n---\n# 10-26\n- [ ] 写月报\n",
    "notes/cache.md": "---\ndate: 2026-10-27\n---\n# 缓存\n缓存穿透与布隆过滤器。\n",
}


async def _post_review(client, *, kind: str, period_key: str, **extra):
    token = await helpers.csrf(client)
    body = {"kind": kind, "period_key": period_key, **extra}
    return await client.post(f"{API}/reviews", json=body, headers=helpers.csrf_headers(token))


@pytest.mark.asyncio
async def test_daily_review_generates_seven_sections(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="review1@example.com", files=DAILY_FILES
    )

    response = await _post_review(client, kind="daily", period_key="2026-10-27")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "created"
    assert body["review_id"]
    state = await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(body["run_id"]))
    assert state in {"succeeded", "human_gate"}, state

    sections = admin_conn.execute(
        "SELECT section_key, ordinal, content_md FROM review_sections "
        "WHERE workspace_id = %s AND review_id = %s AND version = 1 ORDER BY ordinal",
        (workspace_id, body["review_id"]),
    ).fetchall()
    assert [row[1] for row in sections] == list(range(1, 8))
    assert [row[0] for row in sections] == [
        "today_tasks",
        "learning_summary",
        "journal_events",
        "mistakes",
        "methods",
        "tomorrow_actions",
        "one_line_summary",
    ]
    assert all(row[2].strip() for row in sections)

    # 顺延任务进入 today_tasks
    structured = admin_conn.execute(
        "SELECT structured_json FROM review_sections WHERE workspace_id = %s "
        "AND review_id = %s AND version = 1 AND section_key = 'today_tasks'",
        (workspace_id, body["review_id"]),
    ).fetchone()[0]
    assert structured["carried_over"]
    assert structured["carried_over"][0]["text"] == "写月报"


@pytest.mark.asyncio
async def test_empty_day_uses_sentinels_when_allowed(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="review2@example.com", files={}
    )
    rejected = await _post_review(client, kind="daily", period_key="2026-10-27")
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "REVIEW_NO_SOURCE_DATA"

    accepted = await _post_review(client, kind="daily", period_key="2026-10-27", allow_empty=True)
    assert accepted.status_code == 201, accepted.text
    body = accepted.json()
    state = await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(body["run_id"]))
    assert state == "succeeded"

    from mylife_contract.constants import SENTINELS

    rows = admin_conn.execute(
        "SELECT section_key, content_md FROM review_sections WHERE workspace_id = %s "
        "AND review_id = %s AND version = 1",
        (workspace_id, body["review_id"]),
    ).fetchall()
    assert len(rows) == 7
    for section_key, content in rows:
        assert content == SENTINELS[section_key]


@pytest.mark.asyncio
async def test_active_run_conflict_and_version_bump(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="review3@example.com", files=DAILY_FILES
    )
    first = await _post_review(client, kind="daily", period_key="2026-10-27")
    assert first.status_code == 201, first.text
    first_body = first.json()

    conflict = await _post_review(client, kind="daily", period_key="2026-10-27")
    assert conflict.status_code == 409
    details = conflict.json()["error"]["details"]
    assert details["existing_run_id"] == first_body["run_id"]

    await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(first_body["run_id"]))

    second = await _post_review(client, kind="daily", period_key="2026-10-27")
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["version"] == 2
    assert second_body["review_id"] == first_body["review_id"]
    await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(second_body["run_id"]))

    versions = admin_conn.execute(
        "SELECT version FROM review_versions WHERE workspace_id = %s AND review_id = %s "
        "ORDER BY version",
        (workspace_id, first_body["review_id"]),
    ).fetchall()
    assert [row[0] for row in versions] == [1, 2]

    # 旧版本仍可查
    detail = await client.get(f"{API}/reviews/{first_body['review_id']}/versions/1")
    assert detail.status_code == 200
    assert detail.json()["version"] == 1


@pytest.mark.asyncio
async def test_review_detail_and_list(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="review4@example.com", files=DAILY_FILES
    )
    created = await _post_review(client, kind="daily", period_key="2026-10-27")
    body = created.json()
    await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(body["run_id"]))

    detail = await client.get(f"{API}/reviews/{body['review_id']}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["kind"] == "daily"
    assert len(payload["sections"]) == 7
    assert payload["content_md"]
    assert payload["versions"]

    listing = await client.get(f"{API}/reviews?kind=daily")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["period_key"] == "2026-10-27"


@pytest.mark.asyncio
async def test_review_quota_audit(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="review5@example.com", files=DAILY_FILES
    )
    from mylife_api.ratelimit import get_limiter

    limiter = get_limiter()
    key = f"quota:review:ws:{workspace_id}"
    limiter._memory[key] = (50, int(__import__("time").time()) + 3600)

    response = await _post_review(client, kind="daily", period_key="2026-10-27")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "QUOTA_EXCEEDED"

    actions = admin_conn.execute(
        "SELECT action FROM audit_logs WHERE workspace_id = %s AND action = %s",
        (workspace_id, "review.create_rejected"),
    ).fetchall()
    assert actions


@pytest.mark.asyncio
async def test_period_validation_and_max_revisions(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="review6@example.com", files=DAILY_FILES
    )
    invalid = await _post_review(client, kind="daily", period_key="2026-13-40")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "REVIEW_PERIOD_INVALID"

    future = await _post_review(client, kind="daily", period_key="2999-01-01")
    assert future.status_code == 422

    too_many = await _post_review(
        client,
        kind="daily",
        period_key="2026-10-27",
        options={"max_revisions": 5},
    )
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "VALIDATION_ERROR"
