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
    "evidence_items",
    "review_sections",
    "review_entries",
    "review_citations",
    "review_versions",
    "review_search",
    "topic_items",
    "topic_occurrences",
    "delivery_records",
    "delivery_events",
    "review_confirmations",
    "idempotency_keys",
    "topic_files",
    "outbox_events",
    "deletion_requests",
    "export_jobs",
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
    review_b = uuid7()
    conn.execute(
        "INSERT INTO reviews (id, workspace_id, kind, period_key, title, content) "
        "VALUES (%s, %s, 'daily', '2026-10-27', 'B 的日报', '正文')",
        (review_b, ws_b),
    )
    run_b = uuid7()
    conn.execute(
        "INSERT INTO agent_runs (id, workspace_id, kind, period_key, state, idem_key, review_id) "
        "VALUES (%s, %s, 'daily', '2026-10-27', 'succeeded', %s, %s)",
        (run_b, ws_b, f"rls:{run_b}", review_b),
    )
    snap_b = uuid7()
    conn.execute(
        "INSERT INTO source_snapshots "
        "(id, workspace_id, commit_sha, manifest_digest, doc_count, as_of) "
        "VALUES (%s, %s, 'sha-b', 'digest-b', 1, now())",
        (snap_b, ws_b),
    )
    conn.execute(
        "INSERT INTO evidence_items (id, workspace_id, run_id, evidence_id, snapshot_id, "
        "source_type, source_path, quote, quote_hash) "
        "VALUES (%s, %s, %s, 'e1', %s, 'task', 'b/task.md', 'B 证据', %s)",
        (uuid7(), ws_b, run_b, snap_b, "b" * 64),
    )
    conn.execute(
        "INSERT INTO review_sections "
        "(id, workspace_id, review_id, version, section_key, ordinal, content_md) "
        "VALUES (%s, %s, %s, 1, 'today_tasks', 1, 'B 章节')",
        (uuid7(), ws_b, review_b),
    )
    conn.execute(
        "INSERT INTO review_entries "
        "(id, workspace_id, review_id, version, section_key, item_type, "
        "canonical_key, fields_json) VALUES (%s, %s, %s, 1, 'mistakes', 'error', 'k-b', '{}')",
        (uuid7(), ws_b, review_b),
    )
    conn.execute(
        "INSERT INTO review_citations "
        "(id, workspace_id, review_id, version, claim_id, evidence_id, "
        "quote, quote_hash) VALUES (%s, %s, %s, 1, 'c1', 'e1', 'B 引用', %s)",
        (uuid7(), ws_b, review_b, "b" * 64),
    )
    conn.execute(
        "INSERT INTO review_versions "
        "(id, workspace_id, review_id, version, content_md, content_hash) "
        "VALUES (%s, %s, %s, 1, 'B 正文', %s)",
        (uuid7(), ws_b, review_b, "b" * 64),
    )
    conn.execute(
        "INSERT INTO review_search (id, workspace_id, review_id, version, kind, period_key, "
        "title, path, content_hash) VALUES (%s, %s, %s, 1, 'daily', '2026-10-27', 'B 日报', "
        "'wiki/复盘/每日复盘/2026/2026-10-27.md', %s)",
        (uuid7(), ws_b, review_b, "b" * 64),
    )
    topic_b = uuid7()
    conn.execute(
        "INSERT INTO topic_items "
        "(id, workspace_id, topic, canonical_key, title, first_seen, last_seen) "
        "VALUES (%s, %s, 'errors', 'k-b', 'B 专题', CURRENT_DATE, CURRENT_DATE)",
        (topic_b, ws_b),
    )
    conn.execute(
        "INSERT INTO topic_occurrences "
        "(id, workspace_id, item_id, source_date, source_path, quote) "
        "VALUES (%s, %s, %s, CURRENT_DATE, 'b/note.md', 'B 发生')",
        (uuid7(), ws_b, topic_b),
    )
    conn.execute(
        "INSERT INTO delivery_records (id, workspace_id, review_id, version, target, status) "
        "VALUES (%s, %s, %s, 1, 'object_store', 'pending')",
        (uuid7(), ws_b, review_b),
    )
    conn.execute(
        "INSERT INTO delivery_events "
        "(id, workspace_id, provider_delivery_id, event, payload_digest) "
        "VALUES (%s, %s, %s, 'ping', %s)",
        (uuid7(), ws_b, f"delivery-{uuid7()}", "d" * 64),
    )
    conn.execute(
        "INSERT INTO review_confirmations "
        "(id, workspace_id, user_id, token_hash, kind, period_key, expires_at) "
        "VALUES (%s, %s, %s, %s, 'daily', '2026-10-27', now() + interval '10 minutes')",
        (uuid7(), ws_b, user_b, "t" * 64),
    )
    conn.execute(
        "INSERT INTO idempotency_keys "
        "(id, workspace_id, user_id, endpoint, key, request_hash, expires_at) "
        "VALUES (%s, %s, %s, 'POST /reviews', %s, %s, now() + interval '1 day')",
        (uuid7(), ws_b, user_b, f"idem-{uuid7()}", "h" * 64),
    )
    conn.execute(
        "INSERT INTO topic_files (id, workspace_id, topic, path) "
        "VALUES (%s, %s, 'errors', 'wiki/复盘/专题/错误清单.md')",
        (uuid7(), ws_b),
    )
    conn.execute(
        "INSERT INTO outbox_events (id, workspace_id, topic, idempotency_key) "
        "VALUES (%s, %s, 'review.scheduled', %s)",
        (uuid7(), ws_b, f"outbox-{uuid7()}"),
    )
    conn.execute(
        "INSERT INTO deletion_requests "
        "(id, workspace_id, scope, purge_at, requested_by) "
        "VALUES (%s, %s, 'workspace', now() + interval '30 days', %s)",
        (uuid7(), ws_b, user_b),
    )
    conn.execute(
        "INSERT INTO export_jobs (id, workspace_id, requested_by) "
        "VALUES (%s, %s, %s)",
        (uuid7(), ws_b, user_b),
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

    await db_session.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws_a})
    await db_session.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": user_a})
    count = (
        await db_session.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), params)
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
        ("review_sections", "reviews"),
        ("review_entries", "reviews"),
        ("review_citations", "reviews"),
        ("review_versions", "reviews"),
        ("review_search", "reviews"),
        ("delivery_records", "reviews"),
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
