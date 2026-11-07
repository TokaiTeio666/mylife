"""认证与租户验收（AC-01/02/03/05/06/07、SEC-01/02/03/05/08/24）。"""

from __future__ import annotations

import httpx
import pytest

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db


def _codes(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_register_success_creates_personal_workspace(client, admin_conn):
    """AC-01 / AC-03：注册成功、Argon2id、自动创建“我的知识库”。"""
    helpers.insert_invite(admin_conn, "INV-REGISTER01")
    response = await helpers.register(
        client, invite_code="INV-REGISTER01", email="alice@example.com", display_name="Alice"
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["user"]["email"] == "alice@example.com"
    assert body["workspace"]["name"] == "我的知识库"
    assert body["workspace"]["role"] == "owner"
    assert body["session"]["active_workspace_id"] == body["workspace"]["id"]
    assert body["session"]["csrf_token"]

    # Cookie 属性（AC-06）
    cookies = " | ".join(response.headers.get_list("set-cookie")).lower()
    assert "mylife_session=" in cookies
    assert "httponly" in cookies
    assert "secure" in cookies
    assert "samesite=lax" in cookies
    assert "path=/" in cookies

    # Argon2id 哈希；无明文
    password_hash = admin_conn.execute(
        "SELECT password_hash FROM users WHERE email = %s", ("alice@example.com",)
    ).fetchone()[0]
    assert password_hash.startswith("$argon2id$")
    plaintext = helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM users WHERE password_hash = %s",
        ("correct-horse-9",),
    )
    assert plaintext == 0

    # 自动创建的 workspace 与成员角色
    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM workspace_members WHERE workspace_id = %s AND role = 'owner'",
        (body["workspace"]["id"],),
    ) == 1
    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM documents WHERE workspace_id = %s",
        (body["workspace"]["id"],),
    ) == 0

    # 审计（AC-07 / SEC-24）
    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM audit_logs WHERE action = 'user.registered'",
    ) == 1

    me = await client.get(f"{API}/auth/me")
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["active_workspace_id"] == body["workspace"]["id"]
    assert "chat" in me_body["capabilities"]
    assert me_body["limits"]["daily_budget"] == {
        "input_tokens": 100_000,
        "output_tokens": 20_000,
    }


@pytest.mark.asyncio
async def test_register_requires_invite(client):
    token = await helpers.csrf(client)
    response = await client.post(
        f"{API}/auth/register",
        json={
            "email": "noinvite@example.com",
            "password": "12345678",
            "display_name": "无邀请码",
        },
        headers=helpers.csrf_headers(token),
    )
    assert response.status_code == 403
    assert _codes(response) == "AUTH_INVITE_REQUIRED"


@pytest.mark.asyncio
async def test_register_invalid_expired_used_invite(client, admin_conn):
    helpers.insert_invite(admin_conn, "INV-NOTEXIST1")
    response = await helpers.register(
        client, invite_code="INV-WRONGCODE", email="a1@example.com"
    )
    assert response.status_code == 400
    assert _codes(response) == "AUTH_INVITE_INVALID"

    helpers.insert_invite(admin_conn, "INV-EXPIRED01", expires_in_days=-1)
    response = await helpers.register(
        client, invite_code="INV-EXPIRED01", email="a2@example.com"
    )
    assert response.status_code == 400
    assert _codes(response) == "AUTH_INVITE_EXPIRED"

    used_by = helpers.insert_user(admin_conn, email="used-by@example.com")
    helpers.insert_invite(admin_conn, "INV-USEDCODE", used=True, used_by=used_by)
    response = await helpers.register(
        client, invite_code="INV-USEDCODE", email="a3@example.com"
    )
    assert response.status_code == 400
    assert _codes(response) == "AUTH_INVITE_USED"


@pytest.mark.asyncio
async def test_weak_password_and_csrf(client, admin_conn):
    helpers.insert_invite(admin_conn, "INV-WEAKPASS1")
    response = await helpers.register(
        client, invite_code="INV-WEAKPASS1", email="weak@example.com", password="1234567"
    )
    assert response.status_code == 422
    assert _codes(response) == "AUTH_WEAK_PASSWORD"

    # 缺少 CSRF：403 且无副作用（SEC-03）
    response = await client.post(
        f"{API}/auth/register",
        json={
            "email": "nocsrf@example.com",
            "password": "12345678",
            "display_name": "无 CSRF",
            "invite_code": "INV-WEAKPASS1",
        },
    )
    assert response.status_code == 403
    assert _codes(response) == "AUTH_CSRF_INVALID"
    assert helpers.row_count(
        admin_conn, "SELECT count(*) FROM users WHERE email = 'nocsrf@example.com'"
    ) == 0


@pytest.mark.asyncio
async def test_workspace_id_in_body_rejected(client, admin_conn):
    """SEC-08 / §1.4：入参声明 workspace_id 即 422。"""
    helpers.insert_invite(admin_conn, "INV-WSFORBID1")
    token = await helpers.csrf(client)
    response = await client.post(
        f"{API}/auth/register",
        json={
            "email": "wsid@example.com",
            "password": "12345678",
            "display_name": "注入",
            "invite_code": "INV-WSFORBID1",
            "workspace_id": "00000000-0000-0000-0000-000000000000",
        },
        headers=helpers.csrf_headers(token),
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "WORKSPACE_ID_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_request_id_is_propagated_to_errors(client):
    """错误信封的 request_id 必须与响应头一致（防止 contextvar 丢失）。"""
    response = await client.get(f"{API}/auth/me")
    assert response.status_code == 401
    body = response.json()
    assert body["request_id"].startswith("req_")
    assert body["request_id"] == response.headers["X-Request-Id"]


@pytest.mark.asyncio
async def test_unauthenticated_business_endpoints_401(client):
    """SEC-01。"""
    for path in ("/auth/me", "/workspaces", "/documents/tree"):
        response = await client.get(f"{API}{path}")
        assert response.status_code == 401, path
        assert _codes(response) == "AUTH_SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_login_and_logout(client, admin_conn):
    helpers.insert_invite(admin_conn, "INV-LOGINOUT1")
    assert (
        await helpers.register(
            client, invite_code="INV-LOGINOUT1", email="login@example.com"
        )
    ).status_code == 201

    token = helpers.current_csrf(client)
    logout = await client.post(
        f"{API}/auth/logout", headers=helpers.csrf_headers(token)
    )
    assert logout.status_code == 204
    assert (await client.get(f"{API}/auth/me")).status_code == 401

    response = await helpers.login(client, email="login@example.com")
    assert response.status_code == 200, response.text
    assert (await client.get(f"{API}/auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_login_failure_rate_limit(client, admin_conn):
    """AC-01：同 IP/邮箱连续失败，第 6 次 429；文案不区分邮箱/密码。"""
    helpers.insert_invite(admin_conn, "INV-RATELIMIT")
    assert (
        await helpers.register(
            client, invite_code="INV-RATELIMIT", email="ratelimit@example.com"
        )
    ).status_code == 201

    for _ in range(5):
        response = await helpers.login(
            client, email="ratelimit@example.com", password="wrong-password"
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "邮箱或密码不正确"

    blocked = await helpers.login(
        client, email="ratelimit@example.com", password="wrong-password"
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "AUTH_ACCOUNT_LOCKED"
    assert "Retry-After" in blocked.headers

    # 不存在的邮箱返回相同文案与状态码，不泄露账号是否存在
    unknown = await helpers.login(client, email="unknown@example.com", password="wrong")
    assert unknown.status_code == 401
    assert unknown.json()["error"]["message"] == "邮箱或密码不正确"


@pytest.mark.asyncio
async def test_logout_all_revokes_every_device(client, admin_conn, client_factory):
    """AC-06 / SEC-02。"""
    helpers.insert_invite(admin_conn, "INV-LOGOUTALL")
    assert (
        await helpers.register(
            client, invite_code="INV-LOGOUTALL", email="multi@example.com"
        )
    ).status_code == 201

    second = client_factory()
    assert (await helpers.login(second, email="multi@example.com")).status_code == 200
    assert (await second.get(f"{API}/auth/me")).status_code == 200

    response = await client.post(
        f"{API}/auth/logout-all",
        headers=helpers.csrf_headers(helpers.current_csrf(client)),
    )
    assert response.status_code == 204

    assert (await client.get(f"{API}/auth/me")).status_code == 401
    revoked = await second.get(f"{API}/auth/me")
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "AUTH_SESSION_REVOKED"

    rows = admin_conn.execute(
        "SELECT token_version, revoked_at FROM sessions "
        "WHERE user_id = (SELECT id FROM users WHERE email = 'multi@example.com')"
    ).fetchall()
    assert rows
    assert all(row[0] == 2 for row in rows)
    assert all(row[1] is not None for row in rows)


@pytest.mark.asyncio
async def test_admin_invite_codes_require_platform_admin(
    client, admin_conn, client_factory
):
    """AC-02 / SEC-30 前置：邀请码只能由平台管理员创建。"""
    helpers.insert_user(admin_conn, email="admin@example.com", platform_admin=True)
    assert (await helpers.login(client, email="admin@example.com")).status_code == 200

    response = await client.post(
        f"{API}/admin/invite-codes",
        json={"count": 1, "expires_in_days": 30},
        headers=helpers.csrf_headers(helpers.current_csrf(client)),
    )
    assert response.status_code == 201, response.text
    code = response.json()["items"][0]["code"]
    assert len(code) >= 8
    assert helpers.row_count(
        admin_conn, "SELECT count(*) FROM invite_codes WHERE code = %s", (code,)
    ) == 1
    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM audit_logs WHERE action = 'admin.invite_codes_created'",
    ) == 1

    # 普通用户被拒
    helpers.insert_invite(admin_conn, "INV-NORMALUSR")
    normal = client_factory()
    assert (
        await helpers.register(
            normal, invite_code="INV-NORMALUSR", email="normal@example.com"
        )
    ).status_code == 201
    denied = await normal.post(
        f"{API}/admin/invite-codes",
        json={"count": 1, "expires_in_days": 30},
        headers=helpers.csrf_headers(helpers.current_csrf(normal)),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ADMIN_REQUIRED"


@pytest.mark.asyncio
async def test_concurrent_register_with_same_code_only_one_succeeds(
    admin_conn, client_factory
):
    """AC-02：同码并发注册恰好 1 次成功。"""
    import asyncio

    helpers.insert_invite(admin_conn, "INV-CONCUR0001")
    first = client_factory()
    second = client_factory()

    response_a, response_b = await asyncio.gather(
        helpers.register(
            first, invite_code="INV-CONCUR0001", email="concurrent-a@example.com"
        ),
        helpers.register(
            second, invite_code="INV-CONCUR0001", email="concurrent-b@example.com"
        ),
    )
    statuses = sorted([response_a.status_code, response_b.status_code])
    assert statuses == [201, 400]
    failed = response_a if response_a.status_code != 201 else response_b
    assert failed.json()["error"]["code"] == "AUTH_INVITE_USED"
    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM users WHERE email LIKE %s",
        ("concurrent-%",),
    ) == 1


@pytest.mark.asyncio
async def test_register_used_code_second_time_rejected(
    client, admin_conn, client_factory
):
    """SEC-05：同码二次注册拒绝，used_at 非空。"""
    helpers.insert_invite(admin_conn, "INV-ONESHOT01")
    first = await helpers.register(
        client, invite_code="INV-ONESHOT01", email="one@example.com"
    )
    assert first.status_code == 201

    other = client_factory()
    second = await helpers.register(
        other, invite_code="INV-ONESHOT01", email="two@example.com"
    )
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "AUTH_INVITE_USED"

    used_at = admin_conn.execute(
        "SELECT used_at FROM invite_codes WHERE code = 'INV-ONESHOT01'"
    ).fetchone()[0]
    assert used_at is not None
