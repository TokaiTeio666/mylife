"""导入与文档验收（AC-09…AC-15；SEC-13/14）。"""

from __future__ import annotations

import os
import zipfile
from uuid import UUID

import pytest
from mylife_retrieval.embedding import hash_embedding
from mylife_worker.db import job_session
from mylife_worker.indexer import SourceFile, ingest_files
from mylife_worker.markdown import parse_document
from mylife_worker.zip_source import ZipLimits, ZipRejected, extract_zip

from mylife_api.settings import settings
from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

INVITE = "INV-PHASE1A01"


async def _user(client, admin_conn, email: str) -> tuple[str, str]:
    helpers.insert_invite(admin_conn, INVITE, expires_in_days=30)
    response = await helpers.register(client, invite_code=INVITE, email=email)
    assert response.status_code == 201, response.text
    body = response.json()
    return body["workspace"]["id"], body["user"]["id"]


def test_parser_frontmatter_headings_links():
    text_content = """---
title: 缓存笔记
tags: [redis, 缓存, 性能]
date: 2026-10-27
---
# 一级标题
正文段落。

## 二级标题

### 三级标题
- 列表
| a | b |
|---|---|
| 1 | 2 |

```python
print("hi")
```

[[目标笔记|别名]] [[第二篇]]
"""
    parsed = parse_document("日记/2026/10.27.md", text_content)
    assert parsed.title == "缓存笔记"
    assert parsed.tags == ["redis", "缓存", "性能"]
    assert str(parsed.occurred_on) == "2026-10-27"
    assert len(parsed.links) == 2
    assert parsed.links[0].target_path == "目标笔记"
    assert parsed.links[0].link_text == "别名"
    headings = [chunk.heading for chunk in parsed.chunks]
    assert "一级标题 > 二级标题 > 三级标题" in headings


@pytest.mark.asyncio
async def test_zip_import_end_to_end(client, admin_conn):
    workspace_id, _ = await _user(client, admin_conn, "ingest1@example.com")
    data = helpers.make_zip(
        {
            "notes/redis.md": """---
title: Redis 缓存
tags: [redis, 缓存]
---
# Redis
缓存穿透与布隆过滤器。

## 方案
[[notes/mysql|MySQL]]
""",
            "notes/mysql.md": "# MySQL\n索引与事务。",
            "日记/2026/10.27.md": "---\ndate: 2026-10-27\n---\n今天学习缓存。\n",
        }
    )
    response = await helpers.upload_zip(client, data=data)
    assert response.status_code == 202, response.text
    job_id = response.json()["job"]["id"]

    await helpers.run_ingest_job(job_id, workspace_id)
    detail = await helpers.job_detail(client, job_id)
    assert detail["job"]["status"] == "succeeded", detail
    assert detail["counters"]["created"] == 3
    assert detail["job"]["progress"]["processed_files"] == 3
    assert detail["snapshot"]["doc_count"] == 3

    assert helpers.row_count(
        admin_conn, "SELECT count(*) FROM documents WHERE workspace_id = %s", (workspace_id,)
    ) == 3

    row = admin_conn.execute(
        "SELECT id, tags, occurred_on FROM documents WHERE workspace_id = %s "
        "AND path = 'notes/redis.md'",
        (workspace_id,),
    ).fetchone()
    assert set(row[1]) == {"redis", "缓存"}

    headings = [
        r[0]
        for r in admin_conn.execute(
            "SELECT heading FROM document_chunks WHERE document_id = %s ORDER BY chunk_ordinal",
            (row[0],),
        ).fetchall()
    ]
    assert any(h and "Redis > 方案" in h for h in headings)

    links = admin_conn.execute(
        "SELECT target_path, link_text FROM document_links WHERE source_document_id = %s",
        (row[0],),
    ).fetchall()
    assert links and links[0][0] == "notes/mysql"

    # 文档详情与 HTML 渲染
    document_id = str(row[0])
    detail_response = await client.get(f"{API}/documents/{document_id}?include=meta,content,links")
    assert detail_response.status_code == 200, detail_response.text
    body = detail_response.json()
    assert "<h1>" in body["content_html"]
    assert "</h1>" in body["content_html"] or "<h1>" in body["content_html"]

    # 目录树按目录层级组织
    tree = await client.get(f"{API}/documents/tree")
    paths = {node["path"] for node in tree.json()["nodes"]}
    assert {"notes", "notes/redis.md", "notes/mysql.md", "日记", "日记/2026/10.27.md"} <= paths


@pytest.mark.asyncio
async def test_ignore_rules(client, admin_conn):
    workspace_id, _ = await _user(client, admin_conn, "ingest-ignore@example.com")
    files: dict[str, str] = {"keep/note.md": "# 保留\n内容"}
    for directory in (".obsidian", ".trash", ".stfolder", ".git", "node_modules"):
        for index in range(5):
            files[f"{directory}/x{index}.md"] = "# 忽略\n"
    response = await helpers.upload_zip(client, data=helpers.make_zip(files))
    assert response.status_code == 202
    job_id = response.json()["job"]["id"]
    await helpers.run_ingest_job(job_id, workspace_id)

    ignored = admin_conn.execute(
        "SELECT count(*) FROM documents WHERE workspace_id = %s AND ("
        "path LIKE %s OR path LIKE %s OR path LIKE %s OR path LIKE %s OR path LIKE %s)",
        (
            workspace_id,
            ".obsidian/%",
            ".trash/%",
            ".stfolder/%",
            ".git/%",
            "node_modules/%",
        ),
    ).fetchone()[0]
    assert ignored == 0
    assert helpers.row_count(
        admin_conn, "SELECT count(*) FROM documents WHERE workspace_id = %s", (workspace_id,)
    ) == 1

    matcher_dirs = {
        ".obsidian",
        ".trash",
        ".stfolder",
        ".git",
        "node_modules",
    }
    from mylife_worker.ignore import DEFAULT_IGNORE_DIRS

    assert matcher_dirs == set(DEFAULT_IGNORE_DIRS)


@pytest.mark.asyncio
async def test_partial_failure_and_retry(client, admin_conn):
    workspace_id, _ = await _user(client, admin_conn, "ingest-partial@example.com")
    files = {f"ok/{i}.md": f"# 标题 {i}\n内容 {i}" for i in range(8)}
    files["bad/one.md"] = "---\ntags: [未闭合\n---\n正文"
    files["bad/two.md"] = "---\n- just\n- a list\n---\n正文"

    upload = await helpers.upload_zip(client, data=helpers.make_zip(files))
    job_id = upload.json()["job"]["id"]
    await helpers.run_ingest_job(job_id, workspace_id)
    detail = await helpers.job_detail(client, job_id)

    assert detail["job"]["status"] == "partial"
    assert detail["counters"]["failed"] == 2
    assert detail["counters"]["created"] == 8
    failed_paths = {item["path"] for item in detail["failed_items"]}
    assert failed_paths == {"bad/one.md", "bad/two.md"}
    assert all(item["reason_code"] == "PARSE_ERROR" for item in detail["failed_items"])

    indexed_before = dict(
        admin_conn.execute(
            "SELECT path, indexed_at FROM documents WHERE workspace_id = %s", (workspace_id,)
        ).fetchall()
    )

    token = helpers.current_csrf(client)
    retry = await client.post(
        f"{API}/ingest/jobs/{job_id}/retry",
        json={"only_failed": True},
        headers=helpers.csrf_headers(token),
    )
    assert retry.status_code == 202, retry.text
    retry_id = retry.json()["id"]
    await helpers.run_ingest_job(retry_id, workspace_id)

    indexed_after = dict(
        admin_conn.execute(
            "SELECT path, indexed_at FROM documents WHERE workspace_id = %s", (workspace_id,)
        ).fetchall()
    )
    for path, value in indexed_before.items():
        assert indexed_after[path] == value


@pytest.mark.asyncio
async def test_zip_upload_size_limit(client, admin_conn, monkeypatch):
    workspace_id, _ = await _user(client, admin_conn, "ingest-limit@example.com")
    monkeypatch.setattr(settings, "max_zip_bytes", 200)

    # 用不可压缩数据，避免 ZIP 压缩把体积降到上限以下
    response = await helpers.upload_zip(
        client, data=helpers.make_zip({"a.md": os.urandom(4096)})
    )
    assert response.status_code == 413
    assert helpers.row_count(
        admin_conn, "SELECT count(*) FROM job_runs WHERE workspace_id = %s", (workspace_id,)
    ) == 0


class CountingEmbedder:
    name = "counting"
    model = "counting-v1"
    dimension = 1024

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [hash_embedding(text, self.dimension) for text in texts]


@pytest.mark.asyncio
async def test_incremental_hash_and_rename(client, admin_conn):
    workspace_id, user_id = await _user(client, admin_conn, "ingest-incr@example.com")
    embedder = CountingEmbedder()
    files = [
        SourceFile(path="notes/a.md", data="# A\n缓存内容".encode()),
        SourceFile(path="notes/b.md", data="# B\n其他内容".encode()),
    ]

    async with job_session(UUID(workspace_id), UUID(user_id)) as session:
        first = await ingest_files(
            session,
            workspace_id=UUID(workspace_id),
            files=files,
            commit_sha=None,
            storage_path="/tmp/x",
            embedder=embedder,
        )
        await session.commit()
    assert first.counters["created"] == 2
    calls_after_first = embedder.calls
    assert calls_after_first > 0

    async with job_session(UUID(workspace_id), UUID(user_id)) as session:
        second = await ingest_files(
            session,
            workspace_id=UUID(workspace_id),
            files=files,
            commit_sha=None,
            storage_path="/tmp/x",
            embedder=embedder,
        )
        await session.commit()
    assert second.counters["unchanged"] == 2
    assert embedder.calls == calls_after_first

    doc_a = admin_conn.execute(
        "SELECT id FROM documents WHERE workspace_id = %s AND path = 'notes/a.md'",
        (workspace_id,),
    ).fetchone()[0]
    chunks_before = {
        str(r[0])
        for r in admin_conn.execute(
            "SELECT id FROM document_chunks WHERE document_id = %s", (doc_a,)
        ).fetchall()
    }

    renamed = [
        SourceFile(path="notes/renamed-a.md", data=files[0].data),
        files[1],
    ]
    async with job_session(UUID(workspace_id), UUID(user_id)) as session:
        third = await ingest_files(
            session,
            workspace_id=UUID(workspace_id),
            files=renamed,
            commit_sha=None,
            storage_path="/tmp/x",
            embedder=embedder,
        )
        await session.commit()
    assert third.counters["updated"] == 1
    assert embedder.calls == calls_after_first

    renamed_id = admin_conn.execute(
        "SELECT id FROM documents WHERE workspace_id = %s AND path = 'notes/renamed-a.md'",
        (workspace_id,),
    ).fetchone()[0]
    assert renamed_id == doc_a
    chunks_after = {
        str(r[0])
        for r in admin_conn.execute(
            "SELECT id FROM document_chunks WHERE document_id = %s", (doc_a,)
        ).fetchall()
    }
    assert chunks_after == chunks_before


@pytest.mark.asyncio
async def test_document_fields_not_null(client, admin_conn):
    workspace_id, _ = await _user(client, admin_conn, "ingest-fields@example.com")
    response = await helpers.upload_zip(
        client, data=helpers.make_zip({"a/note.md": "# 标题\n正文"})
    )
    job_id = response.json()["job"]["id"]
    await helpers.run_ingest_job(job_id, workspace_id)

    nulls = admin_conn.execute(
        "SELECT count(*) FROM documents WHERE workspace_id = %s AND ("
        "path IS NULL OR title IS NULL OR size IS NULL OR mtime IS NULL "
        "OR content_hash IS NULL OR workspace_id IS NULL OR tags IS NULL)",
        (workspace_id,),
    ).fetchone()[0]
    assert nulls == 0
    lengths = admin_conn.execute(
        "SELECT DISTINCT length(content_hash) FROM documents WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchall()
    assert lengths == [(64,)]


@pytest.mark.asyncio
async def test_html_render_sanitizes_scripts(client, admin_conn):
    workspace_id, _ = await _user(client, admin_conn, "ingest-xss@example.com")
    markdown = """# 渲染
<script>alert(1)</script>

```python
print(1)
```

| a | b |
|---|---|
| 1 | 2 |

![图片](a/img/x.png)
"""
    files = {"a/b/c.md": markdown, "a/img/x.png": b"\x89PNG\r\n\x1a\nfake"}
    response = await helpers.upload_zip(client, data=helpers.make_zip(files))
    job_id = response.json()["job"]["id"]
    await helpers.run_ingest_job(job_id, workspace_id)

    document_id = admin_conn.execute(
        "SELECT id FROM documents WHERE workspace_id = %s AND path = 'a/b/c.md'",
        (workspace_id,),
    ).fetchone()[0]
    detail = await client.get(f"{API}/documents/{document_id}")
    html = detail.json()["content_html"]
    assert "<table>" in html
    assert "<code" in html
    assert "<img" in html
    assert "<script" not in html

    content = await client.get(f"{API}/documents/{document_id}/content?format=html")
    assert content.status_code == 200
    assert "Content-Security-Policy" in content.headers
    assert "<script" not in content.text

    asset = await client.get(f"{API}/documents/{document_id}/asset?path=a/img/x.png")
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("image/png")


def test_zip_traversal_and_bomb_rejected(tmp_path):
    traversal = helpers.make_zip({"../../evil.md": "x"})
    zip_path = tmp_path / "evil.zip"
    zip_path.write_bytes(traversal)
    with pytest.raises(ZipRejected):
        extract_zip(zip_path, tmp_path / "out")
    assert not (tmp_path / "evil.md").exists()

    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.md", "A" * 2_000_000)
    with pytest.raises(ZipRejected):
        extract_zip(bomb, tmp_path / "out2", ZipLimits(max_ratio=2))
    assert not (tmp_path / "out2" / "big.md").exists()


def test_zip_symlink_entry_rejected(tmp_path):
    import stat

    target = tmp_path / "link.zip"
    with zipfile.ZipFile(target, "w") as archive:
        info = zipfile.ZipInfo("link.md")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(ZipRejected):
        extract_zip(target, tmp_path / "out")
    assert not (tmp_path / "out" / "link.md").exists()
