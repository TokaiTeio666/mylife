"""Git 导入与 SSRF/克隆加固（AC-08；SEC-12/15）。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from mylife_worker import git_source
from mylife_worker.config import settings as worker_settings
from mylife_worker.git_source import GitRejected, validate_git_target

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

INVITE = "INV-GITTEST01"


def _git(*args: str, cwd: Path) -> str:
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=True
    )
    return completed.stdout.strip()


def _make_remote(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    (work / "notes").mkdir()
    for index in range(120):
        (work / "notes" / f"note-{index:03d}.md").write_text(
            f"# 笔记 {index}\n缓存与索引内容 {index}\n", encoding="utf-8"
        )
    _git("add", ".", cwd=work)
    _git("commit", "-m", "main: 120 notes", cwd=work)

    _git("checkout", "-b", "small", cwd=work)
    for index in range(117):
        (work / "notes" / f"note-{index:03d}.md").unlink()
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "small branch", cwd=work)
    _git("checkout", "main", cwd=work)

    remote_dir = tmp_path / "remotes" / "owner"
    remote_dir.mkdir(parents=True)
    remote = remote_dir / "repo.git"
    _git("clone", "--bare", str(work), str(remote), cwd=tmp_path)
    return remote


def _head(remote: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=remote)


@pytest.mark.asyncio
async def test_git_import_and_branch_switch(client, admin_conn, tmp_path, monkeypatch):
    remote = _make_remote(tmp_path)
    monkeypatch.setattr(worker_settings, "git_base_url", remote.parents[1].as_uri())
    monkeypatch.setattr(worker_settings, "allow_local_git", True)

    helpers.insert_invite(admin_conn, INVITE, expires_in_days=30)
    registered = await helpers.register(client, invite_code=INVITE, email="git@example.com")
    assert registered.status_code == 201
    workspace_id = registered.json()["workspace"]["id"]

    token = helpers.current_csrf(client)
    created = await client.post(
        f"{API}/ingest/git",
        json={
            "provider": "github",
            "repo_full_name": "owner/repo",
            "base_branch": "main",
            "auth_type": "pat",
            "credential": "dummy-token",
            "initial_sync": True,
        },
        headers=helpers.csrf_headers(token),
    )
    assert created.status_code == 202, created.text
    body = created.json()
    job_id = body["job"]["id"]
    connection_id = body["connection"]["id"]

    await helpers.run_ingest_job(job_id, workspace_id)
    detail = await helpers.job_detail(client, job_id)
    if detail["job"]["status"] != "succeeded":
        error = admin_conn.execute(
            "SELECT error FROM job_runs WHERE id = %s", (job_id,)
        ).fetchone()
        raise AssertionError(f"initial git job failed: {error}")
    assert detail["job"]["status"] == "succeeded"
    assert detail["counters"]["created"] == 120
    assert detail["snapshot"]["commit_sha"] == _head(remote)

    # 切换分支后同步
    admin_conn.execute(
        "UPDATE git_connections SET base_branch = 'small' WHERE id = %s", (connection_id,)
    )
    token = helpers.current_csrf(client)
    sync = await client.post(
        f"{API}/ingest/sync",
        json={"connection_id": connection_id, "force_refresh": True},
        headers=helpers.csrf_headers(token),
    )
    assert sync.status_code == 202, sync.text
    sync_id = sync.json()["id"]
    await helpers.run_ingest_job(sync_id, workspace_id)
    sync_detail = await helpers.job_detail(client, sync_id)
    if sync_detail["job"]["status"] != "succeeded":
        error = admin_conn.execute(
            "SELECT error FROM job_runs WHERE id = %s", (sync_id,)
        ).fetchone()
        raise AssertionError(f"sync failed: {error}")
    assert sync_detail["job"]["status"] == "succeeded"

    count = helpers.row_count(
        admin_conn, "SELECT count(*) FROM documents WHERE workspace_id = %s", (workspace_id,)
    )
    assert count == 3


@pytest.mark.asyncio
async def test_git_invalid_credential_leaves_documents_untouched(
    client, admin_conn, tmp_path, monkeypatch
):
    # 指向不存在的本地裸仓库，快速失败且不触网。
    monkeypatch.setattr(
        worker_settings, "git_base_url", (tmp_path / "missing").as_uri()
    )
    monkeypatch.setattr(worker_settings, "allow_local_git", True)

    helpers.insert_invite(admin_conn, "INV-GITFAIL01", expires_in_days=30)
    registered = await helpers.register(
        client, invite_code="INV-GITFAIL01", email="gitbad@example.com"
    )
    workspace_id = registered.json()["workspace"]["id"]
    before = helpers.row_count(
        admin_conn, "SELECT count(*) FROM documents WHERE workspace_id = %s", (workspace_id,)
    )

    token = helpers.current_csrf(client)
    created = await client.post(
        f"{API}/ingest/git",
        json={
            "provider": "gitlab",
            "repo_full_name": "owner/does-not-exist",
            "base_branch": "main",
            "auth_type": "pat",
            "credential": "bad-token",
        },
        headers=helpers.csrf_headers(token),
    )
    assert created.status_code == 202
    job_id = created.json()["job"]["id"]
    await helpers.run_ingest_job(job_id, workspace_id)

    job = admin_conn.execute(
        "SELECT status, error FROM job_runs WHERE id = %s", (job_id,)
    ).fetchone()
    assert job[0] == "failed"
    assert "INGEST_" in job[1]
    after = helpers.row_count(
        admin_conn, "SELECT count(*) FROM documents WHERE workspace_id = %s", (workspace_id,)
    )
    assert after == before


def test_ssrf_rejected():
    for url in (
        "http://127.0.0.1/repo.git",
        "https://127.0.0.1/repo.git",
        "https://169.254.169.254/repo.git",
        "https://localhost/repo.git",
        "file:///etc/passwd",
        "ext::sh -c id",
    ):
        with pytest.raises(GitRejected) as excinfo:
            validate_git_target(url, allow_local=False)
        assert excinfo.value.reason_code in {"SSRF_BLOCKED", "INGEST_REPO_UNREACHABLE"}


def test_git_common_args_hardened():
    args = git_source._git_common_args(allow_local=False)  # noqa: SLF001
    joined = " ".join(args)
    assert "core.hooksPath=/dev/null" in joined
    assert "protocol.file.allow=never" in joined
    assert "protocol.ext.allow=never" in joined
    assert "submodule.recurse=false" in joined
    assert "filter.lfs.smudge=" in joined


def test_clone_rejects_symlink(tmp_path):
    """克隆后的工作树不允许符号链接（SEC-11/15）。"""
    work = tmp_path / "symrepo"
    work.mkdir()
    (work / "a.md").write_text("# 正常\n", encoding="utf-8")
    try:
        os.symlink(work / "a.md", work / "link.md")
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不允许创建符号链接")

    with pytest.raises(GitRejected):
        git_source._reject_symlinks(work)  # noqa: SLF001


@pytest.mark.asyncio
async def test_git_ingest_app_auth_type_is_mapped(client, admin_conn, tmp_path, monkeypatch):
    """wire 值 `app` 必须映射为 DB 的 `github_app`（PHASE4-SPEC D21）。"""
    remote = _make_remote(tmp_path)
    monkeypatch.setattr(worker_settings, "git_base_url", remote.parents[1].as_uri())
    monkeypatch.setattr(worker_settings, "allow_local_git", True)
    helpers.insert_invite(admin_conn, "INV-GITAPP01", expires_in_days=30)
    registered = await helpers.register(
        client, invite_code="INV-GITAPP01", email="gitapp@example.com"
    )
    workspace_id = registered.json()["workspace"]["id"]
    token = helpers.current_csrf(client)
    created = await client.post(
        f"{API}/ingest/git",
        json={
            "provider": "github",
            "repo_full_name": "owner/app-repo",
            "base_branch": "main",
            "auth_type": "app",
            "credential": "dummy-token",
            "initial_sync": False,
        },
        headers=helpers.csrf_headers(token),
    )
    assert created.status_code == 202, created.text
    stored = admin_conn.execute(
        "SELECT auth_type FROM git_connections WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()[0]
    assert stored == "github_app"
