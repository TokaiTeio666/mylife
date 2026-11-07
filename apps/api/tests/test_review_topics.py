"""create_review 三步协议与专题首见确认（AC-25 / AC-68 / SEC-23）。"""

from __future__ import annotations

import uuid

import pytest

from mylife_api.db import set_rls_context
from mylife_api.services_review import wait_for_run
from mylife_api.tools import ToolContext, execute_tool
from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

FILES = {
    "日记/2026/10.27.md": "---\ndate: 2026-10-27\n---\n# 10-27\n今天复盘缓存。\n",
}


async def _ctx(session, workspace_id: str, user_id: str, *, run_kind: str = "chat"):
    await set_rls_context(session, workspace_id, user_id)
    return ToolContext(
        workspace_id=uuid.UUID(workspace_id),
        user_id=uuid.UUID(user_id),
        run_id=uuid.uuid4(),
        run_kind=run_kind,
    )


@pytest.mark.asyncio
async def test_preview_confirm_enqueue_protocol(client, admin_conn, db_session):
    workspace_id, user_id = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="confirm1@example.com", files=FILES
    )
    ctx = await _ctx(db_session, workspace_id, user_id)

    before = helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM reviews WHERE workspace_id = %s",
        (workspace_id,),
    )
    preview = await execute_tool(
        db_session, ctx, "create_review", {"kind": "daily", "period_key": "2026-10-27"}
    )
    assert preview.status == "ok", preview.summary
    token = preview.result.confirmation_token
    assert preview.result.preview.estimated_tokens.input > 0
    after_preview = helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM reviews WHERE workspace_id = %s",
        (workspace_id,),
    )
    assert after_preview == before  # preview 零副作用

    confirmed = await execute_tool(
        db_session, ctx, "create_review_confirm", {"confirmation_token": token}
    )
    assert confirmed.status == "ok", confirmed.summary
    assert confirmed.result.confirmed is True
    # 生产路径由请求事务提交；测试里手动提交以让后续请求可见。
    await db_session.commit()
    await set_rls_context(db_session, workspace_id, user_id)
    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM reviews WHERE workspace_id = %s",
        (workspace_id,),
    ) == before  # confirm 仍不入队

    # 重复 confirm → 拒绝
    again = await execute_tool(
        db_session, ctx, "create_review_confirm", {"confirmation_token": token}
    )
    assert again.status == "error"
    assert again.error_code == "TOOL_ARGS_INVALID"

    # enqueue：POST /reviews 携带 token
    csrf = await helpers.csrf(client)
    created = await client.post(
        f"{API}/reviews",
        json={
            "kind": "daily",
            "period_key": "2026-10-27",
            "confirmation_token": token,
        },
        headers=helpers.csrf_headers(csrf),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM reviews WHERE workspace_id = %s",
        (workspace_id,),
    ) == before + 1
    await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(body["run_id"]))


@pytest.mark.asyncio
async def test_unconfirmed_token_rejected_on_enqueue(client, admin_conn, db_session):
    workspace_id, user_id = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="confirm2@example.com", files=FILES
    )
    ctx = await _ctx(db_session, workspace_id, user_id)
    preview = await execute_tool(
        db_session, ctx, "create_review", {"kind": "daily", "period_key": "2026-10-27"}
    )
    token = preview.result.confirmation_token

    csrf = await helpers.csrf(client)
    response = await client.post(
        f"{API}/reviews",
        json={
            "kind": "daily",
            "period_key": "2026-10-27",
            "confirmation_token": token,
        },
        headers=helpers.csrf_headers(csrf),
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "CONFIRMATION_REQUIRED"


@pytest.mark.asyncio
async def test_recursive_create_review_rejected_in_review_run(
    client, admin_conn, db_session
):
    workspace_id, user_id = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="confirm3@example.com", files=FILES
    )
    ctx = await _ctx(db_session, workspace_id, user_id, run_kind="daily")
    result = await execute_tool(
        db_session, ctx, "create_review", {"kind": "daily", "period_key": "2026-10-27"}
    )
    assert result.status == "error"
    assert result.error_code == "TOOL_NOT_ALLOWED"
    await db_session.commit()
    denied = admin_conn.execute(
        "SELECT count(*) FROM audit_logs WHERE workspace_id = %s "
        "AND action = 'review.tool_denied'",
        (workspace_id,),
    ).fetchone()[0]
    assert denied == 1


@pytest.mark.asyncio
async def test_topic_pending_confirm_and_recurrence(client, admin_conn, db_session):
    workspace_id, user_id = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="topic1@example.com", files=FILES
    )
    from mylife_contract.review import ITEM_SCHEMAS
    from mylife_runtime.hashing import canonical_key

    from mylife_api import repo_review

    await set_rls_context(db_session, workspace_id, user_id)
    fields = {
        "event": "漏备份",
        "root_cause": "忘记",
        "cost": "1h",
        "correct_action": "加提醒",
        "recurrence_trigger": "周五",
    }
    ITEM_SCHEMAS["error"].model_validate(fields)
    key = canonical_key("error", fields["root_cause"])

    item = await repo_review.upsert_topic_item(
        db_session,
        workspace_id=uuid.UUID(workspace_id),
        topic="errors",
        canonical_key=key,
        title=fields["event"],
        source_date=__import__("datetime").date(2026, 10, 27),
    )
    await db_session.commit()
    assert item["status"] == "pending"

    # pending 不进入默认视图
    listing = await client.get(f"{API}/topics/错误清单/items")
    assert listing.status_code == 200
    assert listing.json()["items"] == []
    pending_view = await client.get(f"{API}/topics/错误清单/items?status=pending")
    assert len(pending_view.json()["items"]) == 1

    # 确认后转 open
    csrf = await helpers.csrf(client)
    confirmed = await client.post(
        f"{API}/topics/错误清单/items/{item['id']}/confirm",
        json={"decision": "accept"},
        headers=helpers.csrf_headers(csrf),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "open"
    open_view = await client.get(f"{API}/topics/错误清单/items")
    assert len(open_view.json()["items"]) == 1

    # 复发只累加，不新建条目
    await set_rls_context(db_session, workspace_id, user_id)
    again = await repo_review.upsert_topic_item(
        db_session,
        workspace_id=uuid.UUID(workspace_id),
        topic="errors",
        canonical_key=key,
        title=fields["event"],
        source_date=__import__("datetime").date(2026, 10, 28),
    )
    await db_session.commit()
    assert again["id"] == item["id"]
    assert again["occurrence_count"] == 2
    assert again["status"] == "open"

    # 已确认条目再次 confirm → 409
    again_confirm = await client.post(
        f"{API}/topics/错误清单/items/{item['id']}/confirm",
        json={"decision": "accept"},
        headers=helpers.csrf_headers(csrf),
    )
    assert again_confirm.status_code == 409


@pytest.mark.asyncio
async def test_topic_unknown_slug_404(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="topic2@example.com", files=FILES
    )
    response = await client.get(f"{API}/topics/不存在的专题/items")
    assert response.status_code == 404
