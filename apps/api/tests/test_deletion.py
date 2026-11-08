"""删除冷却、purge 与 Git 历史保留（SEC-27）。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db


async def _post(client, path: str, body: dict, headers: dict | None = None):
    token = await helpers.csrf(client)
    merged = {**helpers.csrf_headers(token), **(headers or {})}
    return await client.post(path, json=body, headers=merged)


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


@pytest.mark.asyncio
async def test_cooling_purge_and_git_history_retained(client, admin_conn, tmp_path, monkeypatch):
    from mylife_api import services_deletion
    from mylife_api.settings import settings

    workspace_id, user_id = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="del1@example.com", files={}
    )
    name = admin_conn.execute(
        "SELECT name FROM workspaces WHERE id = %s", (workspace_id,)
    ).fetchone()[0]

    # 已合并的 Git 历史（模拟：本地裸仓库中的提交）
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    (work / "review.md").write_text("# merged review\n", encoding="utf-8")
    _git("add", ".", cwd=work)
    _git("commit", "-m", "chore(review): merged", cwd=work)
    remote = tmp_path / "remote.git"
    _git("clone", "--bare", str(work), str(remote), cwd=tmp_path)
    merged_sha = _git("rev-parse", "HEAD", cwd=remote)

    # 二次确认文本错误 → 422
    wrong = await _post(
        client, f"{API}/deletions", {"scope": "workspace", "confirm_text": "错误名称"}
    )
    assert wrong.status_code == 422, wrong.text

    # D15：没有近 7 天的成功导出时拒绝删除（AC-55 的“先导出”顺序）。
    blocked = await _post(
        client, f"{API}/deletions", {"scope": "workspace", "confirm_text": name}
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["details"]["reason"] == "EXPORT_REQUIRED"

    exported = await _post(
        client,
        f"{API}/exports",
        {"scope": "workspace", "include": ["documents", "reviews"]},
        headers={"Idempotency-Key": "del-export"},
    )
    assert exported.status_code == 202, exported.text

    created = await _post(
        client, f"{API}/deletions", {"scope": "workspace", "confirm_text": name}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["state"] == "cooling"
    assert body["can_cancel"] is True
    assert body["notice"]["will_retain"]
    assert "不承诺" in body["notice"]["notice"]
    deletion_id = body["deletion_id"]

    duplicate = await _post(
        client, f"{API}/deletions", {"scope": "workspace", "confirm_text": name}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DELETE_IN_COOLING"

    # 冷却期内写操作被冻结（除 GET/deletions/auth 外）
    frozen = await _post(
        client, f"{API}/reviews", {"kind": "daily", "period_key": "2026-10-27"}
    )
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["error"]["code"] == "DELETE_IN_COOLING"

    current = await client.get(f"{API}/deletions/current")
    assert current.status_code == 200
    assert current.json()["state"] == "cooling"
    assert current.json()["deletion_id"] == deletion_id

    # 冷却期内撤销
    token = await helpers.csrf(client)
    cancelled = await client.post(
        f"{API}/deletions/{deletion_id}/cancel", headers=helpers.csrf_headers(token)
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"

    # 再次删除并到期 purge
    again = await _post(
        client, f"{API}/deletions", {"scope": "workspace", "confirm_text": name}
    )
    assert again.status_code == 201, again.text
    admin_conn.execute(
        "UPDATE deletion_requests SET purge_at = %s WHERE id = %s",
        (datetime.now(UTC) - timedelta(minutes=1), again.json()["deletion_id"]),
    )
    admin_conn.execute(
        "INSERT INTO documents (id, workspace_id, path, title, content, content_hash, size) "
        "VALUES (%s, %s, 'keep.md', 'x', '正文', %s, 3)",
        (uuid.uuid4(), workspace_id, "h" * 64),
    )
    # 用独立会话执行 purge（避免与请求事务耦合）
    from mylife_api.db import get_session_factory, set_rls_context

    factory = get_session_factory()
    async with factory() as session:
        await set_rls_context(session, workspace_id, None)
        count = await services_deletion.purge_due_deletions(session)
        await session.commit()
    assert count == 1

    for table in ("documents", "reviews", "workspace_settings", "git_connections"):
        remaining = admin_conn.execute(
            f"SELECT count(*) FROM {table} WHERE workspace_id = %s", (workspace_id,)
        ).fetchone()[0]
        assert remaining == 0, f"{table} 仍残留正文"
    # AC-55：purge 后 §9.2 级联清单全部 0 命中（除保留表），且无残留审计。
    from mylife_contract.constants import DELETION_PURGE_TABLES

    retained = {"audit_logs", "deletion_requests", "users", "workspaces", "invite_codes"}
    residual = []
    for table in DELETION_PURGE_TABLES:
        if table in retained or table == "sessions":
            continue
        rows = admin_conn.execute(
            f"SELECT count(*) FROM {table} WHERE workspace_id = %s", (workspace_id,)
        ).fetchone()[0]
        if rows:
            residual.append(table)
    assert residual == [], f"purge 残留：{residual}"
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM audit_logs WHERE action = 'deletion.purge_residual'"
        ).fetchone()[0]
        == 0
    ), "不应出现残留告警"
    owner_id = admin_conn.execute(
        "SELECT owner_id FROM workspaces WHERE id = %s", (workspace_id,)
    ).fetchone()[0]
    sessions = admin_conn.execute(
        "SELECT count(*) FROM sessions WHERE user_id = %s", (owner_id,)
    ).fetchone()[0]
    assert sessions == 0, "sessions 未清理"
    state = admin_conn.execute(
        "SELECT state FROM deletion_requests WHERE workspace_id = %s "
        "ORDER BY created_at DESC LIMIT 1",
        (workspace_id,),
    ).fetchone()[0]
    assert state == "purged"
    audit = admin_conn.execute(
        "SELECT count(*) FROM audit_logs WHERE action = 'deletion.purged' AND target = %s",
        (again.json()["deletion_id"],),
    ).fetchone()[0]
    assert audit == 1

    # 正向断言：Git 历史保留且可读（决策 7A / 补充决策 11）
    assert _git("cat-file", "-t", merged_sha, cwd=remote) == "commit"
    assert "merged" in _git("log", "--pretty=%s", cwd=remote)
    assert await asyncio.to_thread(Path(settings.data_root).is_dir)
