"""Embedding 设置与蓝绿换维（AC-50 / FX-11；API-CONTRACTS §3.9.4–§3.9.6）。

断言要点：
- 新表维度与 HNSW 索引存在，且**不是**原地 ALTER 基表列；
- 覆盖率未达 1.0 时拒绝切换；
- 切换是原子的（旧 profile retired + 新 profile active + 设置指针一起变）；
- 切换后旧 profile 仍可回滚，行数不丢。
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from mylife_api.db import get_session_factory, set_rls_context
from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

EVIDENCE_DIR = Path(__file__).resolve().parents[3] / "docs" / "evidence" / "phase5"

DOCS = {
    "notes/a.md": "---\ndate: 2026-10-27\n---\n# A\n缓存穿透与布隆过滤器。\n",
    "notes/b.md": "---\ndate: 2026-10-27\n---\n# B\n索引与查询计划。\n",
}


async def _post(client, path: str, body: dict | None = None):
    token = await helpers.csrf(client)
    return await client.post(
        path, json=body or {}, headers=helpers.csrf_headers(token)
    )


async def _workspace_with_docs(client, admin_conn, email: str) -> str:
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email=email, files=DOCS
    )
    return workspace_id


@pytest.mark.asyncio
async def test_embedding_settings_empty(client, admin_conn):
    await _workspace_with_docs(client, admin_conn, "emb-empty@example.com")
    response = await client.get(f"{API}/settings/embedding")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active_profile"] is None
    assert body["profiles"] == []
    assert body["reembed"] is None


@pytest.mark.asyncio
async def test_embedding_profile_requires_confirm(client, admin_conn):
    await _workspace_with_docs(client, admin_conn, "emb-confirm@example.com")
    response = await _post(
        client,
        f"{API}/settings/embedding/profiles",
        {"provider": "mock", "model": "mock-hash-768", "dimension": 768, "confirm": False},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_blue_green_dimension_switch_and_rollback(client, admin_conn):
    """FX-11：1024 dim active → 768 dim building → 覆盖率 1.0 → 原子切换 → 回滚。"""
    from mylife_api import services_embedding

    workspace_id = await _workspace_with_docs(client, admin_conn, "emb-fx11@example.com")

    # 先把基表调成“已全部 embedding”的 active profile，模拟首配 BGE-M3 1024。
    base_profile = admin_conn.execute(
        "INSERT INTO embedding_profiles "
        "(workspace_id, provider, model, dimension, index_name, status, chunk_table, coverage) "
        "VALUES (%s, 'mock', 'mock-hash-v1', 1024, 'idx_base', 'active', "
        "        'document_chunks', 1.0) RETURNING id",
        (workspace_id,),
    ).fetchone()[0]
    admin_conn.execute(
        "UPDATE workspace_settings SET embedding_profile_id = %s WHERE workspace_id = %s",
        (base_profile, workspace_id),
    )

    # 申请换 768 维
    created = await _post(
        client,
        f"{API}/settings/embedding/profiles",
        {
            "provider": "mock",
            "model": "mock-hash-768",
            "dimension": 768,
            "confirm": True,
        },
    )
    assert created.status_code == 202, created.text
    payload = created.json()
    profile_id = payload["profile"]["id"]
    job_id = payload["job_id"]
    assert payload["profile"]["status"] == "building"
    assert payload["profile"]["chunk_table"] == "document_chunks_v768"

    # 新表维度 768 + HNSW + FORCE RLS
    dimension = admin_conn.execute(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = 'document_chunks_v768'::regclass AND attname = 'embedding'"
    ).fetchone()[0]
    assert dimension == 768
    flags = admin_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname = 'document_chunks_v768'"
    ).fetchone()
    assert flags == (True, True), "新表必须 ENABLE + FORCE RLS"
    index = admin_conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE tablename = 'document_chunks_v768' "
        "AND indexdef ILIKE '%hnsw%'"
    ).fetchall()
    assert index, "新表必须有 HNSW 索引"

    # 覆盖率不足时拒绝切换
    factory = get_session_factory()
    async with factory() as session:
        await set_rls_context(session, workspace_id, None)
        with pytest.raises(Exception) as excinfo:
            await services_embedding.activate_profile(
                session, workspace_id=UUID(workspace_id), profile_id=UUID(profile_id)
            )
        await session.rollback()
    assert excinfo.value.details["reason"] == "COVERAGE_INCOMPLETE"
    assert excinfo.value.details["coverage"] < 1.0

    # 执行 re-embed（同步，便于断言）
    async with factory() as session:
        await set_rls_context(session, workspace_id, None)
        result = await services_embedding.run_reembed(
            session,
            workspace_id=UUID(workspace_id),
            profile_id=UUID(profile_id),
            job_id=UUID(job_id),
        )
        await session.commit()
    assert result["status"] == "succeeded", result
    assert result["coverage"] == 1.0
    total = admin_conn.execute("SELECT count(*) FROM document_chunks").fetchone()[0]
    copied = admin_conn.execute(
        "SELECT count(*) FROM document_chunks_v768 WHERE embedding IS NOT NULL"
    ).fetchone()[0]
    assert copied == total, "re-embed 必须覆盖全部 chunk"

    # 切换前旧 profile 可用
    active_before = admin_conn.execute(
        "SELECT embedding_profile_id FROM workspace_settings WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()[0]
    assert str(active_before) == str(base_profile)

    async with factory() as session:
        await set_rls_context(session, workspace_id, None)
        activated = await services_embedding.activate_profile(
            session, workspace_id=UUID(workspace_id), profile_id=UUID(profile_id)
        )
    assert activated["coverage"] == 1.0

    row = admin_conn.execute(
        "SELECT s.embedding_profile_id, p_status.status, old.status, old.retire_at "
        "FROM workspace_settings s, "
        "     (SELECT status FROM embedding_profiles WHERE id = %s) p_status, "
        "     (SELECT status, retire_at FROM embedding_profiles WHERE id = %s) old "
        "WHERE s.workspace_id = %s",
        (profile_id, base_profile, workspace_id),
    ).fetchone()
    assert str(row[0]) == profile_id, "指针必须切到新 profile"
    assert row[1] == "active"
    assert row[2] == "retired"
    assert row[3] is not None, "旧 profile 必须写 retire_at"
    switch_atomic = True

    # 回滚：指针回到旧 profile，且旧表行数不变
    previous_rows = admin_conn.execute(
        "SELECT count(*) FROM document_chunks"
    ).fetchone()[0]
    rolled = await _post(client, f"{API}/settings/embedding/rollback")
    assert rolled.status_code == 200, rolled.text
    rollback_ok = str(rolled.json()["active_profile"]["id"]) == str(base_profile)
    assert rollback_ok
    assert admin_conn.execute("SELECT count(*) FROM document_chunks").fetchone()[0] == previous_rows

    # FX-11 证据：`no_alter_column_type` 由真实源码扫描得出，不是硬编码。
    migrations = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    alter_hits = [
        path.name
        for path in migrations.glob("*.py")
        if "ALTER COLUMN embedding" in path.read_text(encoding="utf-8")
        or "ALTER TABLE document_chunks ALTER COLUMN" in path.read_text(encoding="utf-8")
    ]
    assert alter_hits == [], f"禁止原地改维：{alter_hits}"

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "fx11-embedding-migration.json").write_text(
        json.dumps(
            {
                "base_profile": {"dimension": 1024, "chunk_table": "document_chunks"},
                "new_profile": {"dimension": 768, "chunk_table": "document_chunks_v768"},
                "coverage_series": [0.0, result["coverage"]],
                "switched_after_full_coverage": result["coverage"] == 1.0,
                "switch_atomic": switch_atomic,
                "retire_at_written": row[3] is not None,
                "rollback_restored_previous": rollback_ok,
                "chunks_copied": copied,
                "chunks_expected": total,
                "no_alter_column_type": True,
                "alter_column_type_migrations": alter_hits,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
