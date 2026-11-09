"""搜索与检索验收（AC-16…AC-20）。"""

from __future__ import annotations

import pytest
from mylife_retrieval.tokenizer import TOKENIZER_VERSION

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

INVITE = "INV-SEARCH001"


async def _ingest(client, admin_conn, files: dict[str, str | bytes], email: str):
    helpers.insert_invite(admin_conn, INVITE, expires_in_days=30)
    registered = await helpers.register(client, invite_code=INVITE, email=email)
    assert registered.status_code == 201, registered.text
    workspace_id = registered.json()["workspace"]["id"]

    upload = await helpers.upload_zip(client, data=helpers.make_zip(files))
    assert upload.status_code == 202, upload.text
    job_id = upload.json()["job"]["id"]
    await helpers.run_ingest_job(job_id, workspace_id)
    detail = await helpers.job_detail(client, job_id)
    assert detail["job"]["status"] == "succeeded", detail
    return workspace_id


@pytest.mark.asyncio
async def test_rrf_scores_and_determinism(client, admin_conn):
    await _ingest(
        client,
        admin_conn,
        {
            "notes/a.md": "# 笔记一\n缓存穿透是常见问题。\n\n## 方案\n布隆过滤器。",
            "notes/b.md": "# 笔记二\n缓存击穿与雪崩。",
        },
        "search-rrf@example.com",
    )

    first = await client.get(f"{API}/search", params={"q": "缓存穿透", "top_k": 10})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["items"], "应有结果"

    top = body["items"][0]
    expected = 0.0
    if top["rank_lexical"]:
        expected += 1.0 / (60 + top["rank_lexical"])
    if top["rank_vector"]:
        expected += 1.0 / (60 + top["rank_vector"])
    assert abs(top["score"] - expected) < 1e-9
    assert top["matched_by"] == "both"
    # 至少各有一条结果由词法路/向量路贡献（"both" 同时计入两路）
    assert any(item["matched_by"] in {"lexical", "both"} for item in body["items"])
    assert any(item["matched_by"] in {"vector", "both"} for item in body["items"])

    sequences = []
    for _ in range(3):
        response = await client.get(f"{API}/search", params={"q": "缓存穿透", "top_k": 10})
        sequences.append([item["path"] for item in response.json()["items"]])
    assert sequences[0] == sequences[1] == sequences[2]


@pytest.mark.asyncio
async def test_chinese_tokenization_and_top10(client, admin_conn):
    workspace_id = await _ingest(
        client,
        admin_conn,
        {"notes/redis.md": "# Redis\n缓存穿透与布隆过滤器。"},
        "search-token@example.com",
    )
    response = await client.get(f"{API}/search", params={"q": "缓存穿透", "top_k": 10})
    paths = [item["path"] for item in response.json()["items"]]
    assert paths[0] == "notes/redis.md"

    row = admin_conn.execute(
        "SELECT search_text, tokenizer_version FROM documents WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()
    assert row[0].strip() == row[0]
    assert "缓存" in row[0].split()
    assert row[1] == TOKENIZER_VERSION


@pytest.mark.asyncio
async def test_snippet_highlight_and_escaping(client, admin_conn):
    await _ingest(
        client,
        admin_conn,
        {"notes/xss.md": '# 笔记\n<script>alert(1)</script> 缓存穿透的示例。'},
        "search-snippet@example.com",
    )
    response = await client.get(f"{API}/search", params={"q": "缓存穿透"})
    item = response.json()["items"][0]
    html = item["snippet_html"]
    assert "<mark>" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert item["snippet"].strip()
    assert len(item["snippet"]) <= 2000


@pytest.mark.asyncio
async def test_filters_and_mtime_independence(client, admin_conn):
    workspace_id = await _ingest(
        client,
        admin_conn,
        {
            "a/redis.md": "---\ntags: [redis]\ndate: 2026-10-01\n---\n# A\n缓存穿透",
            "b/mysql.md": "---\ntags: [mysql]\ndate: 2026-10-20\n---\n# B\n缓存击穿",
            "b/other.md": "---\ntags: [redis]\ndate: 2026-11-01\n---\n# C\n缓存雪崩",
        },
        "search-filters@example.com",
    )

    base = await client.get(f"{API}/search", params={"q": "缓存", "top_k": 20})
    base_paths = {item["path"] for item in base.json()["items"]}
    assert len(base_paths) == 3

    by_dir = await client.get(
        f"{API}/search", params={"q": "缓存", "path_prefix": "a/", "top_k": 20}
    )
    dir_paths = {item["path"] for item in by_dir.json()["items"]}
    assert dir_paths and dir_paths < base_paths
    assert all(path.startswith("a/") for path in dir_paths)

    by_tag = await client.get(f"{API}/search", params={"q": "缓存", "tags": "redis", "top_k": 20})
    tag_paths = {item["path"] for item in by_tag.json()["items"]}
    assert tag_paths == {"a/redis.md", "b/other.md"}

    by_date = await client.get(
        f"{API}/search",
        params={"q": "缓存", "date_from": "2026-10-15", "date_to": "2026-10-31", "top_k": 20},
    )
    date_paths = {item["path"] for item in by_date.json()["items"]}
    assert date_paths == {"b/mysql.md"}

    by_ext = await client.get(
        f"{API}/search", params={"q": "缓存", "extensions": "md", "top_k": 20}
    )
    assert {item["path"] for item in by_ext.json()["items"]} == base_paths

    combined = await client.get(
        f"{API}/search",
        params={
            "q": "缓存",
            "path_prefix": "b/",
            "tags": "redis",
            "date_from": "2026-10-15",
            "extensions": "md",
            "top_k": 20,
        },
    )
    assert {item["path"] for item in combined.json()["items"]} == {"b/other.md"}

    admin_conn.execute(
        "UPDATE documents SET mtime = '1999-01-01T00:00:00Z' WHERE workspace_id = %s",
        (workspace_id,),
    )
    after = await client.get(f"{API}/search", params={"q": "缓存", "top_k": 20})
    assert {item["path"] for item in after.json()["items"]} == base_paths


@pytest.mark.asyncio
async def test_result_shape(client, admin_conn):
    await _ingest(
        client,
        admin_conn,
        {"notes/shape.md": "# 形状\n缓存穿透"},
        "search-shape@example.com",
    )
    response = await client.get(f"{API}/search", params={"q": "缓存穿透", "top_k": 20})
    for item in response.json()["items"]:
        assert item["path"] and item["title"] and item["snippet"]
        assert item["score"] > 0
        assert item["document_id"] and item["chunk_id"]
        assert item["review_id"] is None
        assert set(item) >= {"heading", "line_range", "occurred_on"}


@pytest.mark.asyncio
async def test_search_validation(client, admin_conn):
    await _ingest(
        client, admin_conn, {"n.md": "# N\n内容"}, "search-invalid@example.com"
    )
    empty = await client.get(f"{API}/search", params={"q": "   "})
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "SEARCH_EMPTY_QUERY"

    bad_ext = await client.get(f"{API}/search", params={"q": "内容", "extensions": "pdf"})
    assert bad_ext.status_code == 422

    too_many = await client.get(
        f"{API}/search", params={"q": " ".join(f"词{i}" for i in range(80))}
    )
    assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_lexical_or_fallback_and_query_embedding_cache(client, admin_conn, monkeypatch):
    from mylife_api import services_search

    services_search.clear_query_embedding_cache()
    await _ingest(
        client,
        admin_conn,
        {"notes/redis.md": "# Redis\n缓存穿透的治理方案是布隆过滤器。"},
        "search-or-fallback@example.com",
    )

    # 自然语言问句包含文档未出现的词（“解决”“什么”“问题”），AND 为 0 时应回退 OR 命中。
    lexical = await client.get(
        f"{API}/search",
        params={"q": "布隆过滤器解决什么问题", "mode": "lexical", "top_k": 5},
    )
    assert lexical.status_code == 200
    items = lexical.json()["items"]
    assert items and items[0]["path"] == "notes/redis.md"
    assert items[0]["matched_by"] == "lexical"

    # 同一查询词连续做两次向量检索，第二次应直接命中进程内 LRU 缓存。
    calls = 0
    real_build = services_search.build_provider

    def counting_build(**kwargs):
        nonlocal calls
        calls += 1
        return real_build(**kwargs)

    monkeypatch.setattr(services_search, "build_provider", counting_build)
    first = await client.get(
        f"{API}/search", params={"q": "布隆过滤器解决什么问题", "mode": "vector"}
    )
    second = await client.get(
        f"{API}/search", params={"q": "布隆过滤器解决什么问题", "mode": "vector"}
    )
    assert first.status_code == 200 and second.status_code == 200
    assert calls == 1

