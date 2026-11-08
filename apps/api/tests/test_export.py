"""导出 ZIP 与对账（AC-55 导出半部 / AC-64）。

断言要点：
- 导出包含 documents 原文、reviews、manifest.json，条目数与 DB 计数相等（差 0）；
- manifest 里每个文件的 sha256 与包内内容一致；
- 需登录 + owner；Idempotency-Key 必填；活跃任务唯一；
- 过期后下载返回 EXPORT_EXPIRED。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

EVIDENCE = Path(__file__).resolve().parents[3] / "docs" / "evidence" / "phase5"

DOCS = {
    "日记/2026/10.27.md": "---\ndate: 2026-10-27\n---\n# 10-27\n缓存穿透。\n",
    "notes/cache.md": "---\ndate: 2026-10-27\n---\n# 缓存\n布隆过滤器。\n",
}


async def _post(client, path: str, body: dict | None = None, headers=None):
    token = await helpers.csrf(client)
    merged = {**helpers.csrf_headers(token), **(headers or {})}
    return await client.post(path, json=body or {}, headers=merged)


@pytest.mark.asyncio
async def test_export_zip_matches_db_counts(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="export1@example.com", files=DOCS
    )
    # 生成一次 daily 复盘，让 reviews/ 非空
    from mylife_api.services_review import wait_for_run

    response = await _post(
        client, f"{API}/reviews", {"kind": "daily", "period_key": "2026-10-27", "allow_empty": True}
    )
    assert response.status_code in {200, 201}, response.text
    import uuid as _uuid

    await wait_for_run(_uuid.UUID(workspace_id), _uuid.UUID(response.json()["run_id"]))

    created = await _post(
        client,
        f"{API}/exports",
        {
            "scope": "workspace",
            "include": [
                "documents",
                "reviews",
                "conversations",
                "settings",
                "usage",
                "audit",
            ],
        },
        headers={"Idempotency-Key": "export-key-1"},
    )
    assert created.status_code == 202, created.text
    export_id = created.json()["export_id"]

    job = await client.get(f"{API}/exports/{export_id}")
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "ready", job.text

    download = await client.get(f"{API}/exports/{export_id}/download")
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "application/zip"
    assert "attachment" in download.headers["content-disposition"]

    archive = zipfile.ZipFile(io.BytesIO(download.content))
    names = archive.namelist()
    manifest = json.loads(archive.read("manifest.json"))
    settings = json.loads(archive.read("settings.json"))
    assert settings["provider_credentials"] == "redacted"
    assert "key_ciphertext" not in json.dumps(settings)

    # 条目数对账（差 0）
    docs_in_db = admin_conn.execute(
        "SELECT count(*) FROM documents WHERE workspace_id = %s", (workspace_id,)
    ).fetchone()[0]
    reviews_in_db = admin_conn.execute(
        "SELECT count(*) FROM reviews r WHERE workspace_id = %s AND version = "
        "(SELECT max(version) FROM reviews x WHERE x.id = r.id)",
        (workspace_id,),
    ).fetchone()[0]
    assert manifest["counts"]["documents"] == docs_in_db
    assert manifest["counts"]["reviews"] == reviews_in_db
    doc_entries = [n for n in names if n.startswith("documents/")]
    review_entries = [n for n in names if n.startswith("reviews/")]
    assert len(doc_entries) == docs_in_db
    assert len(review_entries) == reviews_in_db

    # 逐文件 sha256 与 manifest 一致
    by_path = {item["path"]: item for item in manifest["files"]}
    assert set(by_path) == set(names) - {"manifest.json"}
    for name in names:
        if name == "manifest.json":
            continue
        digest = hashlib.sha256(archive.read(name)).hexdigest()
        assert by_path[name]["sha256"] == digest
        assert by_path[name]["bytes"] == len(archive.read(name))

    # usage.csv 可解析
    usage_rows = list(csv.reader(io.StringIO(archive.read("usage.csv").decode())))
    assert usage_rows[0][0] == "kind"

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "export-reconciliation.json").write_text(
        json.dumps(
            {
                "manifest_entries": len(names),
                "documents_in_db": docs_in_db,
                "documents_in_zip": len(doc_entries),
                "reviews_in_db": reviews_in_db,
                "reviews_in_zip": len(review_entries),
                "count_difference": (docs_in_db - len(doc_entries))
                + (reviews_in_db - len(review_entries)),
                "sha256_all_match": True,
                "categories": sorted(manifest["include"]),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_export_requires_idempotency_key(client, admin_conn):
    await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="export-nokey@example.com"
    )
    response = await _post(client, f"{API}/exports", {"scope": "workspace"})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "REQUIRED"


@pytest.mark.asyncio
async def test_export_active_limit(client, admin_conn):
    await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="export-limit@example.com"
    )
    first = await _post(
        client, f"{API}/exports", {"scope": "workspace"},
        headers={"Idempotency-Key": "k1"},
    )
    assert first.status_code == 202, first.text
    # 第一个导出同步完成后才允许第二个；这里直接插一行活跃任务模拟并发。
    workspace_id = admin_conn.execute(
        "SELECT workspace_id FROM export_jobs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    admin_conn.execute(
        "INSERT INTO export_jobs (id, workspace_id, requested_by, status, scope) "
        "SELECT gen_random_uuid(), %s, requested_by, 'running', 'workspace' "
        "FROM export_jobs WHERE workspace_id = %s LIMIT 1",
        (workspace_id, workspace_id),
    )
    second = await _post(
        client, f"{API}/exports", {"scope": "workspace"},
        headers={"Idempotency-Key": "k2"},
    )
    assert second.status_code == 429, second.text
    assert second.json()["error"]["details"]["reason"] == "ACTIVE_EXPORT"


@pytest.mark.asyncio
async def test_expired_export_cannot_download(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="export-expired@example.com", files=DOCS
    )
    created = await _post(
        client, f"{API}/exports", {"scope": "workspace"},
        headers={"Idempotency-Key": "exp-1"},
    )
    assert created.status_code == 202, created.text
    export_id = created.json()["export_id"]
    admin_conn.execute(
        "UPDATE export_jobs SET status = 'expired' WHERE id = %s", (export_id,)
    )
    download = await client.get(f"{API}/exports/{export_id}/download")
    assert download.status_code == 410, download.text
    assert download.json()["error"]["code"] == "EXPORT_EXPIRED"


@pytest.mark.asyncio
async def test_export_expiry_sweeper(client, admin_conn):
    from mylife_api import services_export
    from mylife_api.db import get_session_factory, set_rls_context

    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="export-sweep@example.com", files=DOCS
    )
    created = await _post(
        client, f"{API}/exports", {"scope": "workspace"},
        headers={"Idempotency-Key": "sweep-1"},
    )
    assert created.status_code == 202
    export_id = created.json()["export_id"]
    admin_conn.execute(
        "UPDATE export_jobs SET expires_at = %s WHERE id = %s",
        (datetime.now(UTC) - timedelta(minutes=1), export_id),
    )
    factory = get_session_factory()
    async with factory() as session:
        await set_rls_context(session, None, None)
        purged = await services_export.purge_expired_exports(session)
    assert purged >= 1
    status = admin_conn.execute(
        "SELECT status FROM export_jobs WHERE id = %s", (export_id,)
    ).fetchone()[0]
    assert status == "expired"
