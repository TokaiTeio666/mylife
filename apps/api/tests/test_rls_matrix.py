"""RLS 跨租户矩阵（AC-56 / SEC-07）。

对每一张租户表：以 B 租户写入 1 行，再以 A 租户上下文查询，必须返回 0 行。
同时断言 ENABLE + FORCE RLS、应用角色 NOBYPASSRLS、复合外键存在。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from mylife_api.ids import uuid7

pytestmark = pytest.mark.db

TENANT_TABLES = [
    "documents",
    "document_chunks",
    "document_links",
    "tasks",
    "reviews",
    "conversations",
    "messages",
    "usage_events",
    "job_runs",
    "workspace_settings",
    "workspace_schedules",
    "workspaces",
    "workspace_members",
    "audit_logs",
    "provider_credentials",
]


def _seed_two_tenants(conn, helpers):
    user_a = helpers.insert_user(conn, email="rls-a@example.com")
    user_b = helpers.insert_user(conn, email="rls-b@example.com")
    ws_a = helpers.insert_workspace(conn, owner_id=user_a, name="A 库")
    ws_b = helpers.insert_workspace(conn, owner_id=user_b, name="B 库")
    return user_a, user_b, ws_a, ws_b


def _insert_tenant_rows(conn, helpers, *, user_b: str, ws_b: str) -> None:
    doc_b = helpers.insert_document(
        conn, workspace_id=ws_b, path="b/secret.md", title="B 的私有笔记"
    )
    conn.execute(
        "INSERT INTO document_chunks "
        "(id, workspace_id, document_id, heading, content, token_count, chunk_ordinal, chunk_hash) "
        "VALUES (%s, %s, %s, 'H', 'B chunk', 2, 1, %s)",
        (uuid7(), ws_b, doc_b, "b" * 64),
    )
    conn.execute(
        "INSERT INTO document_links (id, workspace_id, source_document_id, target_path, link_text) "
        "VALUES (%s, %s, %s, 'target.md', '链接')",
        (uuid7(), ws_b, doc_b),
    )
    conn.execute(
        "INSERT INTO tasks (id, workspace_id, key, text, status, first_seen, last_seen) "
        "VALUES (%s, %s, 'b-task', 'B 的任务', 'open', CURRENT_DATE, CURRENT_DATE)",
        (uuid7(), ws_b),
    )
    conn.execute(
        "INSERT INTO reviews (id, workspace_id, kind, period_key, title, content) "
        "VALUES (%s, %s, 'daily', '2026-10-27', 'B 的日报', '正文')",
        (uuid7(), ws_b),
    )
    conv_b = uuid7()
    conn.execute(
        "INSERT INTO conversations (id, workspace_id, user_id, title) "
        "VALUES (%s, %s, %s, 'B 对话')",
        (conv_b, ws_b, user_b),
    )
    conn.execute(
        "INSERT INTO messages (id, workspace_id, conversation_id, role, content) "
        "VALUES (%s, %s, %s, 'user', 'B 消息')",
        (uuid7(), ws_b, conv_b),
    )
    conn.execute(
        "INSERT INTO usage_events (id, workspace_id, user_id, kind) VALUES (%s, %s, %s, 'chat')",
        (uuid7(), ws_b, user_b),
    )
    conn.execute(
        "INSERT INTO job_runs (id, workspace_id, kind, status) VALUES (%s, %s, 'ingest', 'queued')",
        (uuid7(), ws_b),
    )
    conn.execute(
        "INSERT INTO workspace_settings (workspace_id) VALUES (%s) ON CONFLICT DO NOTHING", (ws_b,)
    )
    conn.execute(
        "INSERT INTO workspace_schedules (id, workspace_id, kind, local_time, timezone) "
        "VALUES (%s, %s, 'daily', '01:00', 'Asia/Shanghai') ON CONFLICT DO NOTHING",
        (uuid7(), ws_b),
    )
    conn.execute(
        "INSERT INTO workspace_members (workspace_id, user_id, role) "
        "VALUES (%s, %s, 'member') ON CONFLICT DO NOTHING",
        (ws_b, user_b),
    )
    conn.execute(
        "INSERT INTO audit_logs (id, workspace_id, user_id, action) "
        "VALUES (%s, %s, %s, 'user.login')",
        (uuid7(), ws_b, user_b),
    )
    conn.execute(
        "INSERT INTO provider_credentials "
        "(id, workspace_id, provider, key_ciphertext, key_version) "
        "VALUES (%s, %s, 'llm', %s, 1) ON CONFLICT DO NOTHING",
        (uuid7(), ws_b, b"\x00\x01\x02"),
    )


@pytest.mark.parametrize("table", TENANT_TABLES)
@pytest.mark.asyncio
async def test_cross_tenant_rows_are_invisible(table, admin_conn, db_session):
    from tests import helpers

    user_a, user_b, ws_a, ws_b = _seed_two_tenants(admin_conn, helpers)
    _insert_tenant_rows(admin_conn, helpers, user_b=user_b, ws_b=ws_b)

    if table == "workspaces":
        where, params = "id = :target", {"target": ws_b}
    else:
        where, params = "workspace_id = :target", {"target": ws_b}

    await db_session.execute(
        text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws_a}
    )
    await db_session.execute(
        text("SELECT set_config('app.user_id', :uid, true)"), {"uid": user_a}
    )
    count = (
        await db_session.execute(
            text(f"SELECT count(*) FROM {table} WHERE {where}"), params
        )
    ).scalar_one()
    assert count == 0, f"{table} 泄露了其他租户的行"
    await db_session.rollback()


def test_rls_is_enabled_and_forced(admin_conn):
    rows = admin_conn.execute(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
    ).fetchall()
    flags = {name: (enabled, forced) for name, enabled, forced in rows}
    for table in TENANT_TABLES:
        assert flags[table] == (True, True), f"{table} 未同时 ENABLE + FORCE RLS"


def test_app_role_has_no_bypassrls(admin_conn):
    bypass = admin_conn.execute(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'mylife_app'"
    ).fetchone()[0]
    assert bypass is False


@pytest.mark.asyncio
async def test_no_context_means_default_deny(db_session):
    count = (await db_session.execute(text("SELECT count(*) FROM documents"))).scalar_one()
    assert count == 0


def test_composite_foreign_keys_exist(admin_conn):
    for table, parent in [
        ("document_chunks", "documents"),
        ("document_links", "documents"),
        ("messages", "conversations"),
    ]:
        row = admin_conn.execute(
            """
            SELECT count(*)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_class p ON p.oid = c.confrelid
            WHERE c.contype = 'f' AND t.relname = %s AND p.relname = %s
            """,
            (table, parent),
        ).fetchone()[0]
        assert row >= 1, f"{table} 缺少指向 {parent} 的复合外键"
