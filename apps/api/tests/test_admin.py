"""管理后台（AC-54 / SEC-30；API-CONTRACTS §3.10）。

断言要点：
- 用户/Workspace/邀请码三列表分页返回；
- 生成邀请码可用；
- 禁用账号后登录 401 且已有会话失效；
- 普通用户访问 /admin → 403 ADMIN_REQUIRED；
- 每次操作写 audit_logs；
- **任何 /admin 响应都不含笔记/复盘/对话正文**（SEC-30）；
- 不允许禁用自己或最后一个 platform_admin。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db


async def _admin_client(client_factory, admin_conn, email="root@example.com"):
    helpers.insert_user(admin_conn, email=email, platform_admin=True)
    client = client_factory()
    assert (await helpers.login(client, email=email)).status_code == 200
    return client


async def _post(client, path: str, body: dict | None = None):
    token = await helpers.csrf(client)
    return await client.post(path, json=body or {}, headers=helpers.csrf_headers(token))


async def _put(client, path: str, body: dict):
    token = await helpers.csrf(client)
    return await client.put(path, json=body, headers=helpers.csrf_headers(token))


@pytest.mark.asyncio
async def test_admin_lists_and_invite_codes(client_factory, admin_conn):
    admin = await _admin_client(client_factory, admin_conn)
    user_id = helpers.insert_user(admin_conn, email="member@example.com")
    workspace_id = helpers.insert_workspace(admin_conn, owner_id=user_id, name="成员库")

    users = await admin.get(f"{API}/admin/users")
    assert users.status_code == 200, users.text
    body = users.json()
    assert any(item["email"] == "member@example.com" for item in body["items"])
    assert all("is_platform_admin" not in item for item in body["items"])
    assert "next_cursor" in body

    workspaces = await admin.get(f"{API}/admin/workspaces")
    assert workspaces.status_code == 200, workspaces.text
    assert any(item["id"] == workspace_id for item in workspaces.json()["items"])
    counts = next(
        item for item in workspaces.json()["items"] if item["id"] == workspace_id
    )["counts"]
    assert set(counts) == {"documents", "reviews", "tokens_month"}

    created = await _post(
        admin, f"{API}/admin/invite-codes", {"count": 2, "expires_in_days": 7}
    )
    assert created.status_code == 201, created.text
    codes = [item["code"] for item in created.json()["items"]]
    assert len(codes) == 2
    assert all(len(code) >= 8 for code in codes)

    listed = await admin.get(f"{API}/admin/invite-codes?status=unused")
    assert listed.status_code == 200
    assert {item["code"] for item in listed.json()["items"]} >= set(codes)

    # 未使用的邀请码可撤销
    token = await helpers.csrf(admin)
    deleted = await admin.delete(
        f"{API}/admin/invite-codes/{codes[0]}", headers=helpers.csrf_headers(token)
    )
    assert deleted.status_code == 204, deleted.text

    # 每次操作都写审计
    actions = {
        row[0]
        for row in admin_conn.execute(
            "SELECT action FROM audit_logs WHERE action LIKE 'admin.%'"
        ).fetchall()
    }
    assert "admin.invite_codes_created" in actions
    assert "admin.invite_code.revoked" in actions


@pytest.mark.asyncio
async def test_non_admin_is_rejected(client, admin_conn):
    helpers.insert_invite(admin_conn, "INV-ADMINREJ")
    assert (
        await helpers.register(
            client, invite_code="INV-ADMINREJ", email="plain@example.com"
        )
    ).status_code == 201
    for path in (
        "/admin/users",
        "/admin/workspaces",
        "/admin/audit-logs",
        "/admin/system-config",
        "/admin/usage?from=2026-01-01T00:00:00Z&to=2026-02-01T00:00:00Z",
    ):
        response = await client.get(f"{API}{path}")
        assert response.status_code == 403, (path, response.text)
        assert response.json()["error"]["code"] == "ADMIN_REQUIRED"


@pytest.mark.asyncio
async def test_disable_user_revokes_sessions(client_factory, admin_conn):
    admin = await _admin_client(client_factory, admin_conn)
    victim_email = "victim@example.com"
    helpers.insert_invite(admin_conn, "INV-VICTIM01")
    victim = client_factory()
    assert (
        await helpers.register(victim, invite_code="INV-VICTIM01", email=victim_email)
    ).status_code == 201
    me = await victim.get(f"{API}/auth/me")
    assert me.status_code == 200

    victim_id = admin_conn.execute(
        "SELECT id FROM users WHERE email = %s", (victim_email,)
    ).fetchone()[0]
    disabled = await _post(
        admin, f"{API}/admin/users/{victim_id}/disable", {"reason": "abuse"}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "disabled"

    # 已有会话失效
    assert (await victim.get(f"{API}/auth/me")).status_code == 401
    # 再次登录被拒
    relogin = await helpers.login(victim, email=victim_email)
    assert relogin.status_code == 401
    assert relogin.json()["error"]["code"] == "AUTH_ACCOUNT_DISABLED"

    # 已禁用再禁用 → 409
    again = await _post(
        admin, f"{API}/admin/users/{victim_id}/disable", {"reason": "x"}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ADMIN_USER_DISABLED"

    enabled = await _post(admin, f"{API}/admin/users/{victim_id}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"
    assert (await helpers.login(victim, email=victim_email)).status_code == 200

    audit = admin_conn.execute(
        "SELECT count(*) FROM audit_logs WHERE action IN "
        "('admin.user.disabled','admin.user.enabled')"
    ).fetchone()[0]
    assert audit >= 2


@pytest.mark.asyncio
async def test_cannot_disable_self(client_factory, admin_conn):
    admin = await _admin_client(client_factory, admin_conn)
    admin_id = admin_conn.execute(
        "SELECT id FROM users WHERE email = 'root@example.com'"
    ).fetchone()[0]

    self_disable = await _post(
        admin, f"{API}/admin/users/{admin_id}/disable", {"reason": "oops"}
    )
    assert self_disable.status_code == 409
    assert self_disable.json()["error"]["details"]["reason"] == "CANNOT_DISABLE_SELF"


@pytest.mark.asyncio
async def test_last_platform_admin_guard(admin_conn, db_session):
    """唯一 active platform_admin 不能再被禁用（防自锁死）。

    通过服务层直接验证：HTTP 路径上只有平台管理员能调用，而调用者本身
    就是一个 active 管理员，因此 `CANNOT_DISABLE_SELF` 会先命中；这条
    防线性检查是给未来“管理员代操作”入口留的兜底。
    """
    from uuid import UUID

    from mylife_api import services_admin
    from mylife_api.db import set_rls_context
    from mylife_api.errors import AppError

    only_admin = helpers.insert_user(
        admin_conn, email="solo@example.com", platform_admin=True
    )
    actor = helpers.insert_user(admin_conn, email="actor@example.com")
    await set_rls_context(db_session, None, UUID(actor))

    with pytest.raises(AppError) as excinfo:
        await services_admin.disable_user(
            db_session,
            admin_id=UUID(actor),
            user_id=UUID(only_admin),
            reason="x",
        )
    assert excinfo.value.details["reason"] == "LAST_PLATFORM_ADMIN"


@pytest.mark.asyncio
async def test_audit_logs_and_system_config(client_factory, admin_conn):
    admin = await _admin_client(client_factory, admin_conn)
    helpers.insert_invite(admin_conn, "INV-AUDITLOG")
    user = admin_conn.execute(
        "SELECT created_by FROM invite_codes WHERE code = 'INV-AUDITLOG'"
    ).fetchone()[0]
    admin_conn.execute(
        "INSERT INTO audit_logs (id, workspace_id, user_id, action, target, detail) "
        "VALUES (gen_random_uuid(), NULL, %s, 'user.login', 'x', "
        "        '{\"api_key\": \"sk-should-not-leak\"}'::jsonb)",
        (user,),
    )

    logs = await admin.get(f"{API}/admin/audit-logs?action=user.login")
    assert logs.status_code == 200, logs.text
    items = logs.json()["items"]
    assert items
    # detail 中的敏感键必须被剔除（SEC-30 / SEC-19）
    assert all("api_key" not in item["detail"] for item in items)

    config = await admin.get(f"{API}/admin/system-config")
    assert config.status_code == 200
    keys = {item["key"]: item for item in config.json()["items"]}
    assert "export_ttl_days" in keys
    assert keys["export_ttl_days"]["source"] == "env"
    assert keys["export_ttl_days"]["locked"] is True

    locked = await _put(
        admin, f"{API}/admin/system-config", {"key": "export_ttl_days", "value": 1}
    )
    assert locked.status_code == 409
    assert locked.json()["error"]["code"] == "ADMIN_SYSTEM_CONFIG_LOCKED"

    unknown = await _put(
        admin, f"{API}/admin/system-config", {"key": "nope", "value": 1}
    )
    assert unknown.status_code == 422


@pytest.mark.asyncio
async def test_admin_usage_is_cross_workspace_but_metadata_only(
    client_factory, admin_conn
):
    admin = await _admin_client(client_factory, admin_conn)
    now = datetime.now(UTC)
    for index, tokens in enumerate((100, 200)):
        user_id = helpers.insert_user(admin_conn, email=f"usage{index}@example.com")
        workspace_id = helpers.insert_workspace(admin_conn, owner_id=user_id)
        admin_conn.execute(
            "INSERT INTO usage_events (id, workspace_id, user_id, kind, "
            "prompt_tokens, completion_tokens) VALUES (gen_random_uuid(), %s, %s, "
            "'chat', %s, %s)",
            (workspace_id, user_id, tokens, tokens),
        )
    start = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = await admin.get(f"{API}/admin/usage?from={start}&to={end}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totals"]["prompt_tokens"] == 300
    assert len(body["items"]) == 2
    # 只允许聚合字段（SEC-30）
    for item in body["items"]:
        assert set(item) <= {
            "workspace_id",
            "workspace_name",
            "prompt_tokens",
            "completion_tokens",
            "calls",
            "cost",
        }


@pytest.mark.asyncio
async def test_admin_responses_never_carry_content(client_factory, admin_conn):
    """SEC-30：管理端不提供任何正文读取路径。"""
    admin = await _admin_client(client_factory, admin_conn)
    payload = await _post(
        admin, f"{API}/admin/invite-codes", {"count": 1, "expires_in_days": 1}
    )
    assert payload.status_code == 201
    username = "content-user@example.com"
    helpers.insert_invite(admin_conn, "INV-CONTENT1")
    member = client_factory()
    registered = await helpers.register(
        member, invite_code="INV-CONTENT1", email=username
    )
    assert registered.status_code == 201
    workspace_id = registered.json()["workspace"]["id"]
    user_id = registered.json()["user"]["id"]

    secret = "这是绝不能出现在管理端响应里的笔记正文"
    admin_conn.execute(
        "INSERT INTO documents (id, workspace_id, path, title, content, "
        "content_hash, size) VALUES (gen_random_uuid(), %s, 'secret.md', "
        "'t', %s, %s, 10)",
        (workspace_id, secret, "a" * 64),
    )
    admin_conn.execute(
        "INSERT INTO reviews (id, workspace_id, kind, period_key, title, content) "
        "VALUES (gen_random_uuid(), %s, 'daily', '2026-10-27', 't', %s)",
        (workspace_id, secret),
    )

    for path in (
        "/admin/users",
        f"/admin/users/{user_id}",
        "/admin/workspaces",
        f"/admin/workspaces/{workspace_id}",
        "/admin/audit-logs",
    ):
        response = await admin.get(f"{API}{path}")
        assert response.status_code == 200, (path, response.text)
        assert secret not in response.text, f"{path} 泄露了正文"
