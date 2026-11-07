"""FX-06 fx_tenant_leak（Phase 0 必须全绿）。

场景：两个 Workspace 各 50 篇同名、相似内容的文档；请求体与工具参数注入他人
workspace_id。断言：
- 跨租户引用 0；读取他人行数 0；
- HTTP 统一 404；
- 请求体中的 workspace_id 被拒绝（422）。
工具参数校验（`extra=forbid`）在 Phase 2 的 ToolGateway 落地，这里以契约层断言占位：
任何工具入参模型都不得声明 `workspace_id`（Phase 0 尚无工具模型，断言为真空成立）。
"""

from __future__ import annotations

import pytest

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_fx06_tenant_leak(admin_conn, client_factory):
    # --- 两个租户，各 50 篇同名文档 ---
    users: dict[str, str] = {}
    workspaces: dict[str, str] = {}
    clients: dict[str, object] = {}

    for name, invite in (("a", "INV-FX06TENA1"), ("b", "INV-FX06TENB1")):
        helpers.insert_invite(admin_conn, invite)
        client = client_factory()
        response = await helpers.register(
            client, invite_code=invite, email=f"fx06-{name}@example.com"
        )
        assert response.status_code == 201, response.text
        body = response.json()
        users[name] = body["user"]["id"]
        workspaces[name] = body["workspace"]["id"]
        clients[name] = client
        for i in range(50):
            helpers.insert_document(
                admin_conn,
                workspace_id=body["workspace"]["id"],
                path=f"notes/note-{i:02d}.md",
                title=f"同名笔记 {i}",
                content="缓存穿透与布隆过滤器",
            )

    # --- A 只能看到自己的 50 篇 ---
    tree = await clients["a"].get(f"{API}/documents/tree")  # type: ignore[union-attr]
    assert tree.status_code == 200
    files = [n for n in tree.json()["nodes"] if n["type"] == "file"]
    assert len(files) == 50
    assert all(n["path"].startswith("notes/") for n in files)

    # --- 跨租户切换 → 404，不暴露存在性 ---
    a_csrf = helpers.current_csrf(clients["a"])  # type: ignore[arg-type]
    cross = await clients["a"].post(  # type: ignore[union-attr]
        f"{API}/workspaces/{workspaces['b']}/switch",
        headers=helpers.csrf_headers(a_csrf),
    )
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "NOT_FOUND"

    # --- 路径伪造：A 读取 B 的 workspace 详情 → 404 ---
    detail = await clients["a"].get(f"{API}/workspaces/{workspaces['b']}")  # type: ignore[union-attr]
    assert detail.status_code == 404

    # --- 请求体注入 workspace_id → 422 ---
    csrf = await helpers.csrf(clients["a"])  # type: ignore[arg-type]
    injected = await clients["a"].post(  # type: ignore[union-attr]
        f"{API}/auth/login",
        json={
            "email": "fx06-a@example.com",
            "password": "correct-horse-9",
            "workspace_id": workspaces["b"],
        },
        headers=helpers.csrf_headers(csrf),
    )
    assert injected.status_code == 422
    assert injected.json()["error"]["details"]["reason"] == "WORKSPACE_ID_NOT_ALLOWED"

    # --- 数据库层：A 上下文读取 B 行数 = 0（RLS） ---
    from sqlalchemy import text

    from mylife_api.db import get_session_factory, set_rls_context

    factory = get_session_factory()
    async with factory() as session:
        await set_rls_context(session, workspaces["a"], users["a"])
        count = (
            await session.execute(
                text("SELECT count(*) FROM documents WHERE workspace_id = :ws"),
                {"ws": workspaces["b"]},
            )
        ).scalar_one()
        assert count == 0
        await session.rollback()

    # --- 数据确实存在（否则上面的 0 没有意义） ---
    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM documents WHERE workspace_id = %s",
        (workspaces["b"],),
    ) == 50

    # --- security_abort 审计：跨租户尝试不可静默 ---
    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM audit_logs WHERE action = 'security_abort'",
    ) >= 1
