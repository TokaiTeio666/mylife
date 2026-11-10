"""用量对账（AC-53；API-CONTRACTS §3.9.10）。

- token 总数 == SUM(usage_events)（差 0）
- 调用次数 == usage_events 行数
- 复盘次数 == 成功 review run 数（来自 agent_runs，不是 usage_events）
- 按 Workspace 隔离；跨 Workspace 汇总为 0
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from mylife_api.services_review import wait_for_run
from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

DOCS = {
    "日记/2026/10.27.md": "---\ndate: 2026-10-27\n---\n# 10-27\n缓存穿透。\n",
}


async def _post(client, path: str, body: dict):
    token = await helpers.csrf(client)
    return await client.post(path, json=body, headers=helpers.csrf_headers(token))


def _range() -> str:
    now = datetime.now(UTC)
    return (
        (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


@pytest.mark.asyncio
async def test_usage_totals_reconcile_with_db(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="usage1@example.com", files=DOCS
    )

    # 1 次成功复盘（不写 usage_events）
    created = await _post(
        client,
        f"{API}/reviews",
        {"kind": "daily", "period_key": "2026-10-27", "allow_empty": True},
    )
    assert created.status_code in {200, 201}, created.text
    await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(created.json()["run_id"]))

    # 3 次对话
    conversation = await _post(client, f"{API}/conversations", {})
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["id"]
    for index in range(3):
        turn = await _post(
            client,
            f"{API}/conversations/{conversation_id}/turns",
            {"client_turn_id": str(uuid.uuid4()), "message": f"缓存穿透 {index}"},
        )
        assert turn.status_code in {200, 201, 202}, turn.text
        # 等 chat run 落库 usage_events
        from mylife_api.services_chat import wait_for_run as wait_chat

        await wait_chat(
            uuid.UUID(workspace_id), uuid.UUID(turn.json()["run_id"])
        )

    start, end = _range()
    usage = await client.get(f"{API}/settings/usage?from={start}&to={end}")
    assert usage.status_code == 200, usage.text
    totals = usage.json()["totals"]

    prompt = admin_conn.execute(
        "SELECT COALESCE(sum(prompt_tokens),0) FROM usage_events "
        "WHERE workspace_id = %s AND created_at >= %s AND created_at < %s",
        (workspace_id, start, end),
    ).fetchone()[0]
    completion = admin_conn.execute(
        "SELECT COALESCE(sum(completion_tokens),0) FROM usage_events "
        "WHERE workspace_id = %s AND created_at >= %s AND created_at < %s",
        (workspace_id, start, end),
    ).fetchone()[0]
    calls = admin_conn.execute(
        "SELECT count(*) FROM usage_events WHERE workspace_id = %s "
        "AND created_at >= %s AND created_at < %s",
        (workspace_id, start, end),
    ).fetchone()[0]
    succeeded_runs = admin_conn.execute(
        "SELECT count(*) FROM agent_runs WHERE workspace_id = %s "
        "AND kind IN ('daily','monthly','yearly') AND state = 'succeeded'",
        (workspace_id,),
    ).fetchone()[0]

    assert totals["prompt_tokens"] == prompt
    assert totals["completion_tokens"] == completion
    assert totals["calls"] == calls
    assert totals["review_runs"] == succeeded_runs
    assert totals["review_runs"] >= 1
    assert "cost_source" not in totals  # 未启用时不额外扩展契约

    # 跨 Workspace 汇总为 0
    other = helpers.insert_user(admin_conn, email="usage-other@example.com")
    other_ws = helpers.insert_workspace(admin_conn, owner_id=other)
    admin_conn.execute(
        "INSERT INTO usage_events (id, workspace_id, user_id, kind, prompt_tokens, "
        "completion_tokens) VALUES (gen_random_uuid(), %s, %s, 'chat', 999, 999)",
        (other_ws, other),
    )
    again = await client.get(f"{API}/settings/usage?from={start}&to={end}")
    assert again.json()["totals"]["prompt_tokens"] == prompt
    assert 999 not in [again.json()["totals"]["prompt_tokens"]]


@pytest.mark.asyncio
async def test_usage_series_day_bucket_uses_workspace_timezone(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="usage-tz@example.com"
    )
    # UTC 2026-10-26T20:00Z == 上海 2026-10-27 04:00
    admin_conn.execute(
        "INSERT INTO usage_events (id, workspace_id, kind, prompt_tokens, "
        "completion_tokens, created_at) "
        "VALUES (gen_random_uuid(), %s, 'chat', 10, 5, '2026-10-26T20:00:00Z')",
        (workspace_id,),
    )
    response = await client.get(
        f"{API}/settings/usage?from=2026-10-26T00:00:00Z&to=2026-10-29T00:00:00Z&group_by=day"
    )
    assert response.status_code == 200, response.text
    buckets = [item["bucket"] for item in response.json()["series"]]
    assert "2026-10-27" in buckets, buckets
    assert "2026-10-26" not in buckets


@pytest.mark.asyncio
async def test_usage_member_sees_only_own(client, admin_conn):
    workspace_id, owner_id = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="usage-owner@example.com"
    )
    member_id = helpers.insert_user(admin_conn, email="usage-member@example.com")
    admin_conn.execute(
        "INSERT INTO workspace_members (workspace_id, user_id, role) "
        "VALUES (%s, %s, 'member')",
        (workspace_id, member_id),
    )
    for user_id, tokens in ((owner_id, 100), (member_id, 7)):
        admin_conn.execute(
            "INSERT INTO usage_events (id, workspace_id, user_id, kind, "
            "prompt_tokens, completion_tokens) VALUES (gen_random_uuid(), %s, %s, "
            "'chat', %s, %s)",
            (workspace_id, user_id, tokens, tokens),
        )

    # owner 看到全部
    start, end = _range()
    owner_view = await client.get(f"{API}/settings/usage?from={start}&to={end}")
    assert owner_view.json()["totals"]["prompt_tokens"] == 107

    # member 只看到自己
    assert owner_view.status_code == 200
    from mylife_api.security import hash_password

    admin_conn.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (hash_password("correct-horse-9"), member_id),
    )
    member_client = __import__("httpx").AsyncClient(
        transport=__import__("httpx").ASGITransport(
            app=__import__("mylife_api.main", fromlist=["app"]).app
        ),
        base_url="https://testserver",
    )
    try:
        assert (
            await helpers.login(member_client, email="usage-member@example.com")
        ).status_code == 200
        token = await helpers.csrf(member_client)
        switched = await member_client.post(
            f"{API}/workspaces/{workspace_id}/switch", headers=helpers.csrf_headers(token)
        )
        assert switched.status_code == 200, switched.text
        member_view = await member_client.get(
            f"{API}/settings/usage?from={start}&to={end}"
        )
        assert member_view.status_code == 200, member_view.text
        assert member_view.json()["totals"]["prompt_tokens"] == 7
    finally:
        await member_client.aclose()
