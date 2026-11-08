"""LLM 设置与连接测试（AC-49；API-CONTRACTS §3.9.1–§3.9.3）。

断言要点：
- `key_ciphertext` 是密文，不等于明文，且不含明文子串；
- 日志与错误响应中明文出现次数为 0；
- 测试连接成功返回 ok；失败返回可读错误码且**不落库**（行数与内容不变）。
"""

from __future__ import annotations

import logging

import pytest

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

SECRET = "sk-test-0123456789abcdef0123456789abcdef"


async def _put(client, body: dict):
    token = await helpers.csrf(client)
    return await client.put(
        f"{API}/settings/llm", json=body, headers=helpers.csrf_headers(token)
    )


async def _post(client, path: str, body: dict):
    token = await helpers.csrf(client)
    return await client.post(path, json=body, headers=helpers.csrf_headers(token))


@pytest.mark.asyncio
async def test_llm_settings_defaults_when_unconfigured(client, admin_conn, monkeypatch):
    """无 DB 行时回落环境变量；测试环境默认 mock，因此状态为 valid。"""
    from mylife_api.settings import settings as app_settings

    monkeypatch.setattr(app_settings, "llm_provider", "deepseek", raising=False)
    monkeypatch.setattr(app_settings, "llm_api_key", None, raising=False)
    monkeypatch.setattr(app_settings, "llm_base_url", None, raising=False)
    monkeypatch.setattr(app_settings, "llm_model", "deepseek-chat", raising=False)

    await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="llm-default@example.com"
    )
    response = await client.get(f"{API}/settings/llm")
    assert response.status_code == 200, response.text
    body = response.json()
    # 无 DB 行 + 环境变量无密钥 → missing
    assert body["credential_status"] == "missing"
    assert body["provider"] == "deepseek"
    assert "api_key" not in body
    assert "key_ciphertext" not in body


@pytest.mark.asyncio
async def test_llm_settings_mock_env_reports_valid(client, admin_conn):
    """环境变量为 mock 时无需密钥，状态为 valid。"""
    await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="llm-mock@example.com"
    )
    response = await client.get(f"{API}/settings/llm")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "mock"
    assert body["credential_status"] == "valid"


@pytest.mark.asyncio
async def test_llm_save_encrypts_and_never_echoes(client, admin_conn, caplog):
    """AC-49：密文落库；日志与响应 0 次明文。"""
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="llm-save@example.com"
    )
    with caplog.at_level(logging.DEBUG):
        response = await _put(
            client,
            {
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "api_key": SECRET,
                "test_before_save": False,
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "deepseek"
    assert body["model"] == "deepseek-chat"
    assert body["credential_status"] == "valid"
    assert body["last_tested_at"] is not None
    # 响应绝不回显明文
    assert SECRET not in response.text

    row = admin_conn.execute(
        "SELECT key_ciphertext, status, base_url, model, provider_name "
        "FROM provider_credentials WHERE workspace_id = %s AND provider = 'llm'",
        (workspace_id,),
    ).fetchone()
    assert row is not None
    ciphertext = bytes(row[0])
    assert ciphertext != SECRET.encode()
    assert SECRET.encode() not in ciphertext
    # AES-256-GCM 信封格式：magic + 版本 + 长度字段
    assert ciphertext.startswith(b"MYL1")
    assert row[1] == "active"
    assert row[2] == "https://api.deepseek.com"
    assert row[4] == "deepseek"

    assert SECRET not in caplog.text
    audit = admin_conn.execute(
        "SELECT detail::text FROM audit_logs WHERE workspace_id = %s "
        "AND action = 'settings.llm.updated'",
        (workspace_id,),
    ).fetchall()
    assert audit, "必须写 settings.llm.updated 审计"
    assert all(SECRET not in (row[0] or "") for row in audit)


@pytest.mark.asyncio
async def test_llm_failed_test_does_not_persist(client, admin_conn, monkeypatch):
    """AC-49：测试失败不新增/不更新 provider_credentials 行。"""
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="llm-fail@example.com"
    )

    async def fail_probe(**kwargs):
        from mylife_contract.settings import ProviderTestError

        return False, 0, None, ProviderTestError(code="LLM_UNAVAILABLE", message="上游不可用")

    monkeypatch.setattr("mylife_api.services_llm._probe", fail_probe)

    before = admin_conn.execute(
        "SELECT count(*) FROM provider_credentials WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()[0]
    response = await _put(
        client,
        {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": SECRET,
            "test_before_save": True,
        },
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "LLM_UNAVAILABLE"
    assert SECRET not in response.text
    after = admin_conn.execute(
        "SELECT count(*) FROM provider_credentials WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()[0]
    assert after == before, "失败的测试不得落库"


@pytest.mark.asyncio
async def test_llm_test_endpoint_is_stateless(client, admin_conn):
    """POST /settings/llm/test 不落库。"""
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="llm-test@example.com"
    )
    response = await _post(
        client,
        f"{API}/settings/llm/test",
        {
            "provider": "mock",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["error"] is None
    rows = admin_conn.execute(
        "SELECT count(*) FROM provider_credentials WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()[0]
    assert rows == 0


@pytest.mark.asyncio
async def test_llm_base_url_rejects_plain_http(client, admin_conn):
    """只允许 https；`http://` 需要私有主机白名单（PHASE5-SPEC D26）。"""
    await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="llm-ssrf@example.com"
    )
    response = await _put(
        client,
        {
            "provider": "deepseek",
            "base_url": "http://example.com/v1",
            "model": "deepseek-chat",
            "api_key": SECRET,
            "test_before_save": False,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "HTTPS_REQUIRED"

    for bad in ("file:///etc/passwd", "gopher://x", "ext::sh"):
        denied = await _put(
            client,
            {
                "provider": "deepseek",
                "base_url": bad,
                "model": "m",
                "api_key": SECRET,
                "test_before_save": False,
            },
        )
        assert denied.status_code == 422, (bad, denied.text)


@pytest.mark.asyncio
async def test_llm_saved_key_is_used_at_runtime(client, admin_conn, monkeypatch):
    """保存后运行时构建必须读到 DB 行，而不是环境变量（PHASE5-SPEC D7）。"""
    from mylife_api import services_llm

    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="llm-runtime@example.com"
    )
    saved = await _put(
        client,
        {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": SECRET,
            "test_before_save": False,
        },
    )
    assert saved.status_code == 200, saved.text

    captured: dict[str, object] = {}

    def fake_build(*, provider, base_url, api_key, model):
        captured.update(
            provider=provider, base_url=base_url, api_key=api_key, model=model
        )
        return object()

    monkeypatch.setattr(services_llm, "build_llm_provider", fake_build)

    from mylife_api.db import get_session_factory, set_rls_context

    factory = get_session_factory()
    async with factory() as session:
        await set_rls_context(session, workspace_id, None)
        await services_llm.build_llm_for_workspace(
            session, workspace_id=__import__("uuid").UUID(workspace_id)
        )
        await session.rollback()
    assert captured["api_key"] == SECRET
    assert captured["model"] == "deepseek-chat"
    assert captured["provider"] == "deepseek"
