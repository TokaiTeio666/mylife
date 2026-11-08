"""Phase 5 安全门禁全量补齐（SEC-01/04/08/13/15/21/22/25/30；PHASE5-SPEC §11）。"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import uuid
import zipfile

import pytest
from mylife_worker import git_source
from mylife_worker.zip_source import ZipLimits, ZipRejected, inspect_zip

from mylife_api.main import app
from tests import helpers
from tests.helpers import API

# 允许匿名访问的公开端点（登录/注册/CSRF 握手/Git webhook 验签/OpenAPI 文档）。
PUBLIC_API_PATHS = {
    "/api/v1/auth/csrf",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/webhooks/git/{provider}",
    "/api/v1/openapi.json",
}


def _sample_path(template: str) -> str:
    sample_uuid = "0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"
    replaced = template
    for name in re.findall(r"\{([^}]+)\}", template):
        if name in {"version", "number"}:
            value = "1"
        elif name == "topic":
            value = "错误清单"
        elif name == "code":
            value = "INV-SAMPLE01"
        elif name == "provider":
            value = "github"
        else:
            value = sample_uuid
        replaced = replaced.replace("{" + name + "}", value)
    return replaced


def _business_routes() -> list[tuple[str, str]]:
    spec = app.openapi()
    pairs: list[tuple[str, str]] = []
    for path, operations in (spec.get("paths") or {}).items():
        if not path.startswith("/api/v1/"):
            continue
        if path in PUBLIC_API_PATHS:
            continue
        for method in operations:
            upper = method.upper()
            if upper in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                pairs.append((upper, path))
    return sorted(set(pairs))


@pytest.mark.db
@pytest.mark.asyncio
async def test_sec01_all_business_routes_require_authentication(client):
    """SEC-01：参数化遍历全部 /api/v1/* 业务路由，未登录一律 401。"""
    routes = _business_routes()
    assert len(routes) >= 40, f"业务路由数量异常：{len(routes)}"
    token = await helpers.csrf(client)
    headers = helpers.csrf_headers(token)

    failures: list[tuple[str, str, int]] = []
    for method, template in routes:
        url = _sample_path(template)
        kwargs: dict[str, object] = {}
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            kwargs["headers"] = headers
            kwargs["json"] = {}
        response = await client.request(method, url, **kwargs)
        if response.status_code != 401:
            failures.append((method, template, response.status_code))
    assert failures == [], f"存在未返回 401 的业务端点：{failures}"


@pytest.mark.db
@pytest.mark.asyncio
async def test_sec04_ten_failed_logins_locked_with_retry_after(client, admin_conn):
    """SEC-04：连续 10 次错误登录触发 429 + Retry-After 锁定窗口。"""
    helpers.insert_invite(admin_conn, "INV-SEC04LOCK")
    assert (
        await helpers.register(
            client, invite_code="INV-SEC04LOCK", email="sec04@example.com"
        )
    ).status_code == 201

    statuses: list[int] = []
    for _ in range(10):
        resp = await helpers.login(
            client, email="sec04@example.com", password="wrong-password"
        )
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            assert int(resp.headers["Retry-After"]) >= 1
            assert resp.json()["error"]["code"] == "AUTH_ACCOUNT_LOCKED"
    assert statuses[:5] == [401] * 5
    assert statuses[5:] == [429] * 5


@pytest.mark.db
@pytest.mark.asyncio
async def test_sec08_query_workspace_id_spoofing_ignored(client_factory, admin_conn):
    """SEC-08：query 伪造 workspace_id 被忽略，始终使用服务端 RunContext。"""
    client_a = client_factory()
    ws_a, _ = await helpers.prepare_workspace_with_docs(
        client_a, admin_conn, email="sec08-a@example.com", files={"a.md": "# A\n仅 A 可见"}
    )
    user_b = helpers.insert_user(admin_conn, email="sec08-b@example.com")
    ws_b = helpers.insert_workspace(admin_conn, owner_id=user_b, name="B 私有库")
    helpers.insert_document(
        admin_conn, workspace_id=ws_b, path="b-secret.md", title="B 机密标题"
    )
    assert ws_a != ws_b

    for path in (
        f"{API}/documents/tree?workspace_id={ws_b}",
        f"{API}/search?q=机密&workspace_id={ws_b}",
        f"{API}/reviews?workspace_id={ws_b}",
        f"{API}/conversations?workspace_id={ws_b}",
    ):
        resp = await client_a.get(path)
        assert resp.status_code == 200, (path, resp.text)
        assert "b-secret.md" not in resp.text
        assert "B 机密标题" not in resp.text


def test_sec13_default_zip_limits_and_caps(tmp_path):
    """SEC-13：默认压缩包/解压总大小/单文件/文件数/压缩比五项上限断言。"""
    defaults = ZipLimits()
    assert defaults.max_compressed_bytes == 200 * 1024 * 1024
    assert defaults.max_uncompressed_bytes == 2 * 1024 * 1024 * 1024
    assert defaults.max_file_bytes == 20 * 1024 * 1024
    assert defaults.max_files == 50_000
    assert defaults.max_ratio == 100

    many = tmp_path / "many.zip"
    with zipfile.ZipFile(many, "w") as archive:
        for idx in range(6):
            archive.writestr(f"n{idx}.md", "ok")
    with pytest.raises(ZipRejected) as excinfo:
        inspect_zip(many, ZipLimits(max_files=5))
    assert excinfo.value.reason_code == "INGEST_ZIP_LIMIT"

    single_big = tmp_path / "single.zip"
    with zipfile.ZipFile(single_big, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("huge.md", b"x" * 2048)
    with pytest.raises(ZipRejected) as excinfo2:
        inspect_zip(single_big, ZipLimits(max_file_bytes=1024))
    assert excinfo2.value.reason_code == "INGEST_ZIP_LIMIT"


def test_sec15_git_clone_does_not_execute_hooks_submodules_or_lfs(tmp_path):
    """SEC-15：含 hooks/submodule/LFS 配置的仓库克隆时不执行钩子或子模块。"""
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    work = tmp_path / "malicious_work"
    work.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=work, env=env, check=True, capture_output=True
    )
    (work / "note.md").write_text("# 安全笔记\n", encoding="utf-8")
    (work / ".gitattributes").write_text(
        "*.bin filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8"
    )
    (work / ".gitmodules").write_text(
        '[submodule "sub"]\n\tpath = sub\n\turl = https://127.0.0.1/evil.git\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "."], cwd=work, env=env, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=work, env=env, check=True, capture_output=True
    )

    bare = tmp_path / "malicious.git"
    subprocess.run(
        ["git", "clone", "--bare", str(work), str(bare)],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
    )
    marker = tmp_path / "hook_executed.marker"
    hook = bare / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\necho pwned > '{marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)

    dest = tmp_path / "cloned"
    cloned = git_source.clone_repository(
        url=bare.as_uri(),
        branch="main",
        destination=dest,
        credential=None,
        allow_local=True,
    )
    assert len(cloned.commit_sha) == 40
    assert not marker.exists(), "Git hook 被意外执行"
    assert not (dest / "sub").exists(), "submodule 被意外拉取"


@pytest.mark.db
@pytest.mark.asyncio
async def test_sec21_svg_asset_is_sanitized_and_sandboxed(client, admin_conn):
    """SEC-21：SVG 资产代理移除脚本与内联事件，并附带 sandbox CSP + nosniff。"""
    malicious_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
        "<script>alert('xss')</script>"
        "<foreignObject><body><iframe src=\"javascript:alert(2)\"></iframe></body></foreignObject>"
        '<a href="javascript:alert(3)"><circle cx="10" cy="10" r="5" onclick="evil()"/></a>'
        "</svg>"
    )
    ws_id, _ = await helpers.prepare_workspace_with_docs(
        client,
        admin_conn,
        email="sec21@example.com",
        files={
            "notes/pic.md": "# SVG\n![图](img/evil.svg)\n",
            "img/evil.svg": malicious_svg.encode("utf-8"),
        },
    )
    doc_id = admin_conn.execute(
        "SELECT id FROM documents WHERE workspace_id = %s AND path = 'notes/pic.md'",
        (ws_id,),
    ).fetchone()[0]

    resp = await client.get(f"{API}/documents/{doc_id}/asset?path=img/evil.svg")
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("Content-Security-Policy") == "sandbox"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    body = resp.text.lower()
    assert "<script" not in body
    assert "<foreignobject" not in body
    assert "onload=" not in body
    assert "onclick=" not in body
    assert "javascript:" not in body
    assert "<circle" in body


@pytest.mark.db
@pytest.mark.asyncio
async def test_sec22_search_and_chat_rate_limits_return_429(client, admin_conn):
    """SEC-22：搜索（60/min）与对话（20/min）超限返回 429 + Retry-After。"""
    await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="sec22@example.com", files={"a.md": "# A\n缓存"}
    )

    search_429 = None
    for _ in range(62):
        resp = await client.get(f"{API}/search?q=缓存")
        if resp.status_code == 429:
            search_429 = resp
            break
    assert search_429 is not None, "搜索第 61 次应触发 429"
    assert int(search_429.headers["Retry-After"]) >= 1
    assert search_429.json()["error"]["code"] == "RATE_LIMITED"

    token = await helpers.csrf(client)
    conv = await client.post(
        f"{API}/conversations", json={}, headers=helpers.csrf_headers(token)
    )
    assert conv.status_code == 201
    conv_id = conv.json()["id"]

    chat_429 = None
    for idx in range(22):
        token = await helpers.csrf(client)
        resp = await client.post(
            f"{API}/conversations/{conv_id}/turns",
            json={"client_turn_id": str(uuid.uuid4()), "message": f"q-{idx}"},
            headers=helpers.csrf_headers(token),
        )
        if resp.status_code == 429:
            chat_429 = resp
            break
    # 等待本事件循环派生的后台 chat 任务落库结束，避免持有连接阻塞后续用例 TRUNCATE。
    from mylife_api import services_chat

    loop = asyncio.get_running_loop()
    current_tasks = [t for t in list(services_chat._tasks) if t.get_loop() is loop]
    if current_tasks:
        await asyncio.gather(*current_tasks, return_exceptions=True)
    assert chat_429 is not None, "对话第 21 次应触发 429"
    assert int(chat_429.headers["Retry-After"]) >= 1
    assert chat_429.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.db
@pytest.mark.asyncio
async def test_sec25_security_abort_run_cannot_be_approved(client, admin_conn):
    """SEC-25：进入 security_abort 的 run 严禁人工 approve 放行。"""
    ws_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="sec25@example.com"
    )
    run_id = uuid.uuid4()
    admin_conn.execute(
        "INSERT INTO agent_runs (id, workspace_id, kind, period_key, state, idem_key) "
        "VALUES (%s, %s, 'daily', '2026-10-27', 'security_abort', %s)",
        (run_id, ws_id, f"sec25:{run_id}"),
    )
    token = await helpers.csrf(client)
    resp = await client.post(
        f"{API}/runs/{run_id}/approve",
        json={"decision_note": "试图强行放行"},
        headers=helpers.csrf_headers(token),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "SECURITY_ABORT"
    state = admin_conn.execute(
        "SELECT state FROM agent_runs WHERE id = %s", (run_id,)
    ).fetchone()[0]
    assert state == "security_abort"


def _collect_keys(obj: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            keys |= _collect_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _collect_keys(item)
    return keys


@pytest.mark.db
@pytest.mark.asyncio
async def test_sec30_admin_field_sets_exclude_all_content_columns(
    client_factory, admin_conn
):
    """SEC-30：/admin/* 全部端点的递归键集合严禁出现任何正文/引用/消息字段。"""
    helpers.insert_user(admin_conn, email="sec30-root@example.com", platform_admin=True)
    admin = client_factory()
    assert (await helpers.login(admin, email="sec30-root@example.com")).status_code == 200

    member = client_factory()
    ws_id, user_id = await helpers.prepare_workspace_with_docs(
        member, admin_conn, email="sec30-user@example.com", files={"a.md": "# T\n机密内容"}
    )

    forbidden = {
        "content",
        "content_md",
        "content_html",
        "quote",
        "messages",
        "sections",
        "citations",
        "key_ciphertext",
        "password_hash",
    }
    endpoints = (
        f"{API}/admin/users",
        f"{API}/admin/users/{user_id}",
        f"{API}/admin/workspaces",
        f"{API}/admin/workspaces/{ws_id}",
        f"{API}/admin/invite-codes",
        f"{API}/admin/audit-logs",
        f"{API}/admin/system-config",
        f"{API}/admin/usage?from=2026-01-01T00:00:00Z&to=2026-12-31T00:00:00Z",
    )
    for url in endpoints:
        resp = await admin.get(url)
        assert resp.status_code == 200, (url, resp.text)
        leaked = _collect_keys(resp.json()) & forbidden
        assert leaked == set(), f"{url} 响应包含禁止字段：{leaked}"
