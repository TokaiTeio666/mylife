"""Workspace 与文档树验收（AC-03/AC-04/AC-05、SEC-06）。"""

from __future__ import annotations

import pytest

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_two_workspaces_are_isolated(client, admin_conn, client_factory):
    """AC-04：切换后文档树只返回当前 Workspace；active_workspace_id 更新。"""
    helpers.insert_invite(admin_conn, "INV-WSTWOSIDE")
    registered = await helpers.register(
        client, invite_code="INV-WSTWOSIDE", email="ws1@example.com"
    )
    assert registered.status_code == 201
    w1 = registered.json()["workspace"]["id"]

    created = await client.post(
        f"{API}/workspaces",
        json={"name": "第二个知识库"},
        headers=helpers.csrf_headers(helpers.current_csrf(client)),
    )
    assert created.status_code == 201, created.text
    w2 = created.json()["id"]

    helpers.insert_document(admin_conn, workspace_id=w1, path="w1/笔记.md", title="W1 笔记")
    helpers.insert_document(admin_conn, workspace_id=w2, path="w2/笔记.md", title="W2 笔记")
    helpers.insert_document(admin_conn, workspace_id=w2, path="w2/资料/深度.md", title="W2 深度")

    tree_w1 = await client.get(f"{API}/documents/tree")
    assert tree_w1.status_code == 200, tree_w1.text
    paths_w1 = {node["path"] for node in tree_w1.json()["nodes"]}
    assert "w1/笔记.md" in paths_w1
    assert not any(p.startswith("w2/") for p in paths_w1)

    switched = await client.post(
        f"{API}/workspaces/{w2}/switch",
        headers=helpers.csrf_headers(helpers.current_csrf(client)),
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["active_workspace_id"] == w2

    tree_w2 = await client.get(f"{API}/documents/tree")
    body = tree_w2.json()
    file_paths = {n["path"] for n in body["nodes"] if n["type"] == "file"}
    assert file_paths == {"w2/笔记.md", "w2/资料/深度.md"}
    assert body["total_documents"] == 2
    assert not (file_paths & {node["path"] for node in tree_w1.json()["nodes"]})

    active = admin_conn.execute(
        "SELECT active_workspace_id FROM sessions WHERE user_id = "
        "(SELECT id FROM users WHERE email = 'ws1@example.com') "
        "AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    assert str(active) == w2


@pytest.mark.asyncio
async def test_switch_to_non_member_returns_404_and_security_audit(
    client, admin_conn, client_factory
):
    """SEC-06 / SEC-25：跨租户切换统一 404，内部记 security_abort。"""
    helpers.insert_invite(admin_conn, "INV-TENANTLEAK")
    assert (
        await helpers.register(
            client, invite_code="INV-TENANTLEAK", email="tenant-a@example.com"
        )
    ).status_code == 201

    other = client_factory()
    helpers.insert_invite(admin_conn, "INV-TENANTOTHER")
    other_registered = await helpers.register(
        other, invite_code="INV-TENANTOTHER", email="tenant-b@example.com"
    )
    assert other_registered.status_code == 201
    other_workspace = other_registered.json()["workspace"]["id"]

    response = await client.post(
        f"{API}/workspaces/{other_workspace}/switch",
        headers=helpers.csrf_headers(helpers.current_csrf(client)),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"

    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM audit_logs WHERE action = 'security_abort' "
        "AND detail ->> 'reason' = 'TENANT_LEAK'",
    ) == 1


@pytest.mark.asyncio
async def test_workspace_detail_and_rename(client, admin_conn):
    helpers.insert_invite(admin_conn, "INV-WSDETAIL1")
    registered = await helpers.register(
        client, invite_code="INV-WSDETAIL1", email="detail@example.com"
    )
    workspace_id = registered.json()["workspace"]["id"]

    detail = await client.get(f"{API}/workspaces/{workspace_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["counts"] == {"documents": 0, "reviews": 0, "open_tasks": 0}
    assert body["settings_summary"]["timezone"] == "Asia/Shanghai"
    assert body["settings_summary"]["reviews_root"] == "wiki/复盘"

    renamed = await client.patch(
        f"{API}/workspaces/{workspace_id}",
        json={"name": "我的主知识库"},
        headers=helpers.csrf_headers(helpers.current_csrf(client)),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "我的主知识库"
    assert helpers.row_count(
        admin_conn, "SELECT count(*) FROM audit_logs WHERE action = 'workspace.renamed'"
    ) == 1

    members = await client.get(f"{API}/workspaces/{workspace_id}/members")
    assert members.status_code == 200
    assert [m["role"] for m in members.json()["items"]] == ["owner"]


@pytest.mark.asyncio
async def test_workspace_quota(client, admin_conn):
    helpers.insert_invite(admin_conn, "INV-WSQUOTA01")
    assert (
        await helpers.register(
            client, invite_code="INV-WSQUOTA01", email="quota@example.com"
        )
    ).status_code == 201

    for i in range(4):
        response = await client.post(
            f"{API}/workspaces",
            json={"name": f"库 {i + 2}"},
            headers=helpers.csrf_headers(helpers.current_csrf(client)),
        )
        assert response.status_code == 201, response.text

    over = await client.post(
        f"{API}/workspaces",
        json={"name": "第六个"},
        headers=helpers.csrf_headers(helpers.current_csrf(client)),
    )
    assert over.status_code == 429
    assert over.json()["error"]["code"] == "QUOTA_EXCEEDED"


@pytest.mark.asyncio
async def test_role_assignment_is_restricted(client, admin_conn):
    """AC-05：首版只能分配 owner；成员管理端点不存在，DB 不新增行。"""
    helpers.insert_invite(admin_conn, "INV-ROLEONLY1")
    registered = await helpers.register(
        client, invite_code="INV-ROLEONLY1", email="role@example.com"
    )
    workspace_id = registered.json()["workspace"]["id"]

    response = await client.post(
        f"{API}/workspaces/{workspace_id}/members",
        json={"user_id": registered.json()["user"]["id"], "role": "admin"},
        headers=helpers.csrf_headers(helpers.current_csrf(client)),
    )
    assert response.status_code in (404, 405)
    assert helpers.row_count(
        admin_conn,
        "SELECT count(*) FROM workspace_members WHERE workspace_id = %s",
        (workspace_id,),
    ) == 1

    roles = {
        row[0]
        for row in admin_conn.execute(
            "SELECT DISTINCT role FROM workspace_members"
        ).fetchall()
    }
    assert roles == {"owner"}


@pytest.mark.asyncio
async def test_document_tree_filters(client, admin_conn):
    helpers.insert_invite(admin_conn, "INV-TREEFILT1")
    registered = await helpers.register(
        client, invite_code="INV-TREEFILT1", email="tree@example.com"
    )
    workspace_id = registered.json()["workspace"]["id"]
    for path in ("a/b/c.md", "a/d.md", "e.md"):
        helpers.insert_document(admin_conn, workspace_id=workspace_id, path=path)

    shallow = await client.get(f"{API}/documents/tree", params={"depth": 2})
    assert shallow.status_code == 200
    shallow_files = {n["path"] for n in shallow.json()["nodes"] if n["type"] == "file"}
    assert shallow_files == {"a/d.md", "e.md"}

    rooted = await client.get(f"{API}/documents/tree", params={"root": "a"})
    assert rooted.status_code == 200
    rooted_files = {n["path"] for n in rooted.json()["nodes"] if n["type"] == "file"}
    assert rooted_files == {"a/b/c.md", "a/d.md"}

    missing = await client.get(f"{API}/documents/tree", params={"root": "不存在"})
    assert missing.status_code == 404
