"""交付（AC-39 / AC-44 / AC-67）与回滚。"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from mylife_api.services_review import wait_for_run
from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

FILES = {
    "日记/2026/10.27.md": "---\ndate: 2026-10-27\n---\n# 10-27\n今天复盘缓存。\n",
    "日记/2026/10.26.md": "---\ndate: 2026-10-26\n---\n# 10-26\n- [ ] 写周报\n",
}


async def _generate(client, workspace_id: str, *, kind: str, period_key: str):
    token = await helpers.csrf(client)
    response = await client.post(
        f"{API}/reviews",
        json={"kind": kind, "period_key": period_key, "allow_empty": True},
        headers=helpers.csrf_headers(token),
    )
    assert response.status_code in {200, 201}, response.text
    body = response.json()
    state = await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(body["run_id"]))
    return body, state


def _git(*args: str, cwd: Path) -> str:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    completed = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    return (completed.stdout or "").strip()


def _make_bare_remote(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    (work / "README.md").write_text("# output\n", encoding="utf-8")
    _git("add", ".", cwd=work)
    _git("commit", "-m", "init", cwd=work)
    remote = tmp_path / "remote.git"
    _git("clone", "--bare", str(work), str(remote), cwd=tmp_path)
    return remote


class _FakeGitHub:
    def __init__(self) -> None:
        self.pulls: dict[int, dict] = {}
        self.merge_calls = 0
        self.stop = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # noqa: D401 - silence
                return

            def _json(self, status: int, payload) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path.count("/") == 2:  # /repos/owner/repo
                    return self._json(200, {"permissions": {"push": True}})
                if parsed.path.endswith("/pulls"):
                    head = parse_qs(parsed.query).get("head", [""])[0]
                    items = [
                        item
                        for item in owner.pulls.values()
                        if item["state"] == "open" and item["head"]["ref"] == head.split(":")[-1]
                    ]
                    return self._json(200, items)
                return self._json(404, {"message": "not found"})

            def do_POST(self):  # noqa: N802
                if self.path.endswith("/pulls"):
                    body = self._body()
                    number = len(owner.pulls) + 1
                    item = {
                        "number": number,
                        "html_url": f"https://example.test/pull/{number}",
                        "state": "open",
                        "title": body.get("title"),
                        "body": body.get("body"),
                        "head": {"ref": body.get("head")},
                        "base": {"ref": body.get("base")},
                    }
                    owner.pulls[number] = item
                    return self._json(201, item)
                return self._json(404, {"message": "not found"})

            def do_PATCH(self):  # noqa: N802
                number = int(self.path.rstrip("/").split("/")[-1])
                item = owner.pulls.get(number)
                if item is None:
                    return self._json(404, {"message": "not found"})
                body = self._body()
                item.update({"title": body.get("title", item["title"]),
                             "body": body.get("body", item["body"])})
                return self._json(200, item)

            def do_PUT(self):  # noqa: N802
                number = int(self.path.rstrip("/").split("/")[-2])
                item = owner.pulls.get(number)
                if item is None:
                    return self._json(404, {"message": "not found"})
                owner.merge_calls += 1
                item["state"] = "closed"
                item["merged"] = True
                return self._json(200, {"merged": True, "sha": "f" * 40})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_github():
    server = _FakeGitHub()
    yield server
    server.close()


def _seed_output_connection(
    admin_conn,
    *,
    workspace_id: str,
    remote: Path,
    api_base_url: str,
    provider: str = "github",
) -> None:
    admin_conn.execute(
        "INSERT INTO git_connections "
        "(id, workspace_id, role, provider, repo_full_name, base_branch, auth_type, "
        " reviews_root, mode, sync_interval_minutes, sync_enabled, remote_url, "
        " api_base_url, credential_status) "
        "VALUES (%s, %s, 'output', %s, 'acme/out', 'main', 'pat', "
        "        'wiki/复盘', 'managed', 0, false, %s, %s, 'valid')",
        (uuid.uuid4(), workspace_id, provider, str(remote), api_base_url),
    )


@pytest.mark.asyncio
async def test_object_store_fallback_bundle_and_zip(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dlv1@example.com", files=FILES
    )
    body, state = await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
    assert state == "succeeded", state

    row = admin_conn.execute(
        "SELECT id, target, status, degraded, manifest_json FROM delivery_records "
        "WHERE workspace_id = %s AND review_id = %s",
        (workspace_id, body["review_id"]),
    ).fetchone()
    assert row is not None
    delivery_id, target, status, degraded, manifest_json = row
    assert target == "object_store"
    assert status == "committed"
    assert degraded is False

    manifest = (
        manifest_json if isinstance(manifest_json, dict) else json.loads(manifest_json)
    )
    paths = {item["path"] for item in manifest["targets"]}
    assert "每日复盘/2026/2026-10-27.md" in paths
    assert "专题/错误清单.md" in paths
    assert "索引.md" in paths

    from mylife_api.settings import settings

    files_dir = (
        Path(settings.data_root)
        / "workspaces"
        / workspace_id
        / "artifacts"
        / "deliveries"
        / str(delivery_id)
        / "files"
    )
    assert (files_dir / "wiki/复盘/每日复盘/2026/2026-10-27.md").exists()

    downloaded = await client.get(f"{API}/deliveries/{delivery_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    zip_path = (
        Path(settings.data_root)
        / "workspaces"
        / workspace_id
        / "artifacts"
        / "deliveries"
        / str(delivery_id)
        / "artifact.zip"
    )
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "wiki/复盘/每日复盘/2026/2026-10-27.md" in names
    assert "manifest.json" in names


@pytest.mark.asyncio
async def test_compat_alias_reads_review_content(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dlv2@example.com", files=FILES
    )
    body, state = await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
    assert state == "succeeded"

    response = await client.get(
        f"{API}/documents/by-path",
        params={"path": "data/reviews/每日复盘/2026/2026-10-27.md"},
    )
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["path"] == "wiki/复盘/每日复盘/2026/2026-10-27.md"
    assert detail["content"]
    assert detail["source_type"] == "generated"


@pytest.mark.asyncio
async def test_same_day_rerun_version_increment_single_path(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dlv3@example.com", files=FILES
    )
    first, state = await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
    assert state == "succeeded"
    second, state2 = await _generate(
        client, workspace_id, kind="daily", period_key="2026-10-27"
    )
    assert state2 == "succeeded"
    assert second["version"] == first["version"] + 1

    versions = admin_conn.execute(
        "SELECT count(*) FROM review_versions WHERE workspace_id = %s AND review_id = %s",
        (workspace_id, first["review_id"]),
    ).fetchone()[0]
    assert versions == 2
    reviews = admin_conn.execute(
        "SELECT version FROM reviews WHERE workspace_id = %s AND id = %s",
        (workspace_id, first["review_id"]),
    ).fetchone()[0]
    assert reviews == 2
    # 同一 review 的产物路径只有一个
    paths = admin_conn.execute(
        "SELECT DISTINCT git_path FROM reviews WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchall()
    assert len(paths) == 1


@pytest.mark.asyncio
async def test_git_delivery_branch_trailers_and_pr_body(
    client, admin_conn, tmp_path, fake_github
):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dlv4@example.com", files=FILES
    )
    remote = _make_bare_remote(tmp_path)
    _seed_output_connection(
        admin_conn, workspace_id=workspace_id, remote=remote, api_base_url=fake_github.base_url
    )
    body, state = await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
    assert state == "succeeded", state

    row = admin_conn.execute(
        "SELECT id, target, status, branch, commit_sha, pr_number, pr_url "
        "FROM delivery_records WHERE workspace_id = %s AND review_id = %s",
        (workspace_id, body["review_id"]),
    ).fetchone()
    assert row is not None
    delivery_id, target, status, branch, commit_sha, pr_number, pr_url = row
    assert target == "git"
    assert status == "pr_open", status
    assert branch == "review/daily/2026-10-27"
    assert pr_number == 1
    assert pr_url

    message = _git(
        "--git-dir", str(remote), "log", "-1", branch, "--format=%B", cwd=remote.parent
    )
    for trailer in ("Run-Id:", "Review-Id:", "Version: 1", "Workspace:"):
        assert trailer in message
    files = _git(
        "--git-dir", str(remote), "ls-tree", "-r", "--name-only", branch,
        cwd=remote.parent,
    )
    assert "wiki/复盘/每日复盘/2026/2026-10-27.md" in files
    assert "wiki/复盘/专题/错误清单.md" in files
    assert "wiki/复盘/索引.md" in files

    pull = fake_github.pulls[1]
    assert pull["title"] == "[复盘] 2026-10-27 每日复盘 (v1)"
    for heading in (
        "## 30 秒结论",
        "## 章节自检",
        "## 证据前 10 条",
        "## 变更文件",
        "## 风险与回滚",
        "## GateResult",
    ):
        assert heading in pull["body"]

    detail = await client.get(f"{API}/deliveries/{delivery_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["manifest"]["branch"] == "review/daily/2026-10-27"


@pytest.mark.asyncio
async def test_git_rerun_reuses_open_pr(client, admin_conn, tmp_path, fake_github):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dlv5@example.com", files=FILES
    )
    remote = _make_bare_remote(tmp_path)
    _seed_output_connection(
        admin_conn, workspace_id=workspace_id, remote=remote, api_base_url=fake_github.base_url
    )
    first, state = await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
    assert state == "succeeded"
    _second, state2 = await _generate(
        client, workspace_id, kind="daily", period_key="2026-10-27"
    )
    assert state2 == "succeeded"
    assert len(fake_github.pulls) == 1, "同 period 重跑必须复用 open PR"
    count = admin_conn.execute(
        "SELECT count(*) FROM delivery_records WHERE workspace_id = %s AND review_id = %s",
        (workspace_id, first["review_id"]),
    ).fetchone()[0]
    assert count == 2  # 每个 version 一条记录，但只有一个 PR


@pytest.mark.asyncio
async def test_auto_merge_matrix_and_audit(client, admin_conn, tmp_path, fake_github):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dlv6@example.com", files=FILES
    )
    admin_conn.execute(
        "UPDATE workspace_settings SET auto_merge_daily = true WHERE workspace_id = %s",
        (workspace_id,),
    )
    remote = _make_bare_remote(tmp_path)
    _seed_output_connection(
        admin_conn, workspace_id=workspace_id, remote=remote, api_base_url=fake_github.base_url
    )
    body, state = await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
    assert state == "succeeded"
    row = admin_conn.execute(
        "SELECT status, auto_merge, merged_commit FROM delivery_records "
        "WHERE workspace_id = %s AND review_id = %s",
        (workspace_id, body["review_id"]),
    ).fetchone()
    assert row[0] == "merged"
    assert row[1] is True
    assert row[2] == "f" * 40
    audit = admin_conn.execute(
        "SELECT count(*) FROM audit_logs WHERE workspace_id = %s "
        "AND action = 'delivery.auto_merged'",
        (workspace_id,),
    ).fetchone()[0]
    assert audit == 1

    # weekly 不自动合并
    weekly, weekly_state = await _generate(
        client, workspace_id, kind="weekly", period_key="2026-W43"
    )
    assert weekly_state in {"succeeded", "needs_human"}, weekly_state
    if weekly_state == "succeeded":
        weekly_row = admin_conn.execute(
            "SELECT status, auto_merge FROM delivery_records WHERE workspace_id = %s "
            "AND review_id = %s",
            (workspace_id, weekly["review_id"]),
        ).fetchone()
        assert weekly_row == ("pr_open", False)


@pytest.mark.asyncio
async def test_rollback_creates_new_version_and_delivers(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dlv7@example.com", files=FILES
    )
    first, state = await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
    assert state == "succeeded"
    second, state2 = await _generate(
        client, workspace_id, kind="daily", period_key="2026-10-27"
    )
    assert state2 == "succeeded"

    token = await helpers.csrf(client)
    response = await client.post(
        f"{API}/reviews/{first['review_id']}/rollback",
        json={"version": 1, "confirm": True},
        headers=helpers.csrf_headers(token),
    )
    assert response.status_code == 202, response.text
    assert response.json()["target_version"] == 1

    versions = admin_conn.execute(
        "SELECT version, content_md FROM review_versions WHERE workspace_id = %s "
        "AND review_id = %s ORDER BY version",
        (workspace_id, first["review_id"]),
    ).fetchall()
    assert [row[0] for row in versions] == [1, 2, 3]
    assert versions[2][1] == versions[0][1]
    assert second["version"] == 2
