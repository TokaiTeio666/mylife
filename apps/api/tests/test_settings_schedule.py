"""设置与调度（AC-43 / AC-51 / AC-52 / FX-09）。"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db


async def _put(client, path: str, body: dict):
    token = await helpers.csrf(client)
    return await client.put(path, json=body, headers=helpers.csrf_headers(token))


@pytest.mark.asyncio
async def test_schedule_defaults_and_update(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="sched1@example.com", files={}
    )
    response = await client.get(f"{API}/settings/schedule")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timezone"] == "Asia/Shanghai"
    assert body["daily_at"].startswith("01:00")
    assert body["weekly_at"].startswith("01:30")
    assert body["yearly_at"].startswith("02:00")
    assert body["catchup_days"] == 7

    updated = await _put(
        client,
        f"{API}/settings/schedule",
        {
            "timezone": "Asia/Shanghai",
            "daily_at": "02:00",
            "weekly_at": "02:30",
            "yearly_at": "03:00",
            "catchup_days": 5,
            "enabled": {"daily": True, "weekly": True, "yearly": False},
        },
    )
    assert updated.status_code == 200, updated.text
    data = updated.json()
    assert data["daily_at"].startswith("02:00")
    assert data["catchup_days"] == 5
    assert data["enabled"]["yearly"] is False
    assert data["next_run_at"]["daily"]
    assert data["next_run_at"]["yearly"] is None

    row = admin_conn.execute(
        "SELECT timezone, daily_at, weekly_at, yearly_at FROM workspace_settings "
        "WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()
    assert row[0] == "Asia/Shanghai"
    schedules = admin_conn.execute(
        "SELECT kind, local_time, enabled, catchup_days FROM workspace_schedules "
        "WHERE workspace_id = %s ORDER BY kind",
        (workspace_id,),
    ).fetchall()
    assert len(schedules) == 3
    assert all(item[3] == 5 for item in schedules)

    # 活跃 run 不被配置变更中断
    run_id = uuid.uuid4()
    admin_conn.execute(
        "INSERT INTO agent_runs (id, workspace_id, kind, period_key, state, idem_key, "
        "  trigger_type) VALUES (%s, %s, 'daily', '2026-10-27', 'drafting', %s, 'manual')",
        (run_id, workspace_id, f"keep:{run_id}"),
    )
    again = await _put(
        client,
        f"{API}/settings/schedule",
        {
            "timezone": "America/New_York",
            "daily_at": "02:00",
            "weekly_at": "02:30",
            "yearly_at": "03:00",
            "catchup_days": 7,
            "enabled": {"daily": True, "weekly": True, "yearly": True},
        },
    )
    assert again.status_code == 200
    state = admin_conn.execute(
        "SELECT state FROM agent_runs WHERE id = %s", (run_id,)
    ).fetchone()[0]
    assert state == "drafting"


@pytest.mark.asyncio
async def test_dispatch_tick_admits_skips_and_catchup(client, admin_conn, monkeypatch):
    from mylife_api import dispatcher

    monkeypatch.setattr("mylife_api.settings.settings.queue_backend", "off")
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="sched2@example.com", files={}
    )
    past = datetime.now(UTC) - timedelta(days=1)
    admin_conn.execute(
        "UPDATE workspace_schedules SET next_run_at = %s, catchup_days = 7 "
        "WHERE workspace_id = %s AND kind = 'daily'",
        (past, workspace_id),
    )

    result = await dispatcher.run_once()
    assert result.get("locked") is not False, result
    runs = admin_conn.execute(
        "SELECT id, period_key, trigger_type, state FROM agent_runs "
        "WHERE workspace_id = %s ORDER BY created_at DESC",
        (workspace_id,),
    ).fetchall()
    assert runs
    run_id, period, trigger, _state = runs[0]
    assert trigger == "schedule"
    assert period

    next_at = admin_conn.execute(
        "SELECT next_run_at FROM workspace_schedules WHERE workspace_id = %s "
        "AND kind = 'daily'",
        (workspace_id,),
    ).fetchone()[0]
    assert next_at is not None and next_at > datetime.now(UTC)

    # 已有成功复盘 → 只记 period_already_succeeded
    # （把所有 daily 置为 delivered，避免跨本地午夜的周期边界差异）
    admin_conn.execute(
        "UPDATE reviews SET status = 'delivered' WHERE workspace_id = %s "
        "AND kind = 'daily'",
        (workspace_id,),
    )
    admin_conn.execute(
        "UPDATE workspace_schedules SET next_run_at = %s, last_skip_reason = NULL "
        "WHERE workspace_id = %s AND kind = 'daily'",
        (past, workspace_id),
    )
    await dispatcher.run_once()
    reason = admin_conn.execute(
        "SELECT last_skip_reason FROM workspace_schedules WHERE workspace_id = %s "
        "AND kind = 'daily'",
        (workspace_id,),
    ).fetchone()[0]
    assert reason == "period_already_succeeded"

    # 停机 10 天 → catchup_window_exceeded
    admin_conn.execute(
        "UPDATE workspace_schedules SET next_run_at = %s, last_skip_reason = NULL "
        "WHERE workspace_id = %s AND kind = 'daily'",
        (datetime.now(UTC) - timedelta(days=10), workspace_id),
    )
    await dispatcher.run_once()
    reason = admin_conn.execute(
        "SELECT last_skip_reason FROM workspace_schedules WHERE workspace_id = %s "
        "AND kind = 'daily'",
        (workspace_id,),
    ).fetchone()[0]
    assert reason == "catchup_window_exceeded"

    # NULL next_run_at（启动补算）
    admin_conn.execute(
        "UPDATE workspace_schedules SET next_run_at = NULL WHERE workspace_id = %s "
        "AND kind = 'weekly'",
        (workspace_id,),
    )
    await dispatcher.run_once()
    weekly_next = admin_conn.execute(
        "SELECT next_run_at FROM workspace_schedules WHERE workspace_id = %s "
        "AND kind = 'weekly'",
        (workspace_id,),
    ).fetchone()[0]
    assert weekly_next is not None


class _PermServer:
    def __init__(self, *, push: bool) -> None:
        self.push = push
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                body = json.dumps({"permissions": {"push": owner.push}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        owner = self
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


def _make_bare(tmp_path: Path) -> Path:
    import subprocess

    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(work)], check=True, capture_output=True)
    (work / "README.md").write_text("# out\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=work, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=work, check=True, capture_output=True
    )
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(work), str(remote)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return remote


@pytest.mark.asyncio
async def test_git_settings_connection_matrix(client, admin_conn, tmp_path):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="sched3@example.com", files={}
    )
    remote = _make_bare(tmp_path)
    good = _PermServer(push=True)
    bad = _PermServer(push=False)
    try:
        payload = {
            "role": "output",
            "provider": "github",
            "repo_full_name": "acme/out",
            "base_branch": "main",
            "auth_type": "pat",
            "credential": "test-token",
            "sync_interval_minutes": 30,
            "remote_url": str(remote),
            "api_base_url": good.base_url,
        }
        response = await _put(client, f"{API}/settings/git", payload)
        assert response.status_code == 200, response.text
        summary = response.json()
        assert summary["role"] == "output"
        assert summary["credential_status"] == "valid"
        assert summary["sync_enabled"] is False  # output 不自动拉取
        row = admin_conn.execute(
            "SELECT credential_ciphertext, auth_type, reviews_root, api_base_url, "
            "  next_sync_at FROM git_connections WHERE workspace_id = %s",
            (workspace_id,),
        ).fetchone()
        assert row[0] is not None and b"test-token" not in bytes(row[0])
        assert row[1] == "pat"
        assert row[2] == "wiki/复盘"
        assert row[4] is None

        listed = await client.get(f"{API}/settings/git")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["role"] == "output"

        # 缺少 contents:write → 拒绝且不保存新连接
        failing = dict(payload)
        failing["repo_full_name"] = "acme/denied"
        failing["api_base_url"] = bad.base_url
        rejected = await _put(client, f"{API}/settings/git", failing)
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"]["code"] == "INGEST_AUTH_FAILED"
        assert rejected.json()["error"]["details"]["reason"] == "MISSING_CONTENTS_WRITE"
        count = admin_conn.execute(
            "SELECT count(*) FROM git_connections WHERE workspace_id = %s "
            "AND repo_full_name = 'acme/denied'",
            (workspace_id,),
        ).fetchone()[0]
        assert count == 0

        # source 连接：sync_interval > 0 → next_sync_at 按新间隔重算
        source_payload = dict(payload)
        source_payload.update({"role": "source", "repo_full_name": "acme/src"})
        source = await _put(client, f"{API}/settings/git", source_payload)
        assert source.status_code == 200, source.text
        first_sync = datetime.fromisoformat(
            admin_conn.execute(
                "SELECT next_sync_at FROM git_connections WHERE workspace_id = %s "
                "AND repo_full_name = 'acme/src'",
                (workspace_id,),
            ).fetchone()[0].isoformat()
        )
        source_payload["sync_interval_minutes"] = 120
        await _put(client, f"{API}/settings/git", source_payload)
        second_sync = admin_conn.execute(
            "SELECT next_sync_at FROM git_connections WHERE workspace_id = %s "
            "AND repo_full_name = 'acme/src'",
            (workspace_id,),
        ).fetchone()[0]
        assert second_sync > first_sync

        token = await helpers.csrf(client)
        deleted = await client.delete(
            f"{API}/settings/git", headers=helpers.csrf_headers(token)
        )
        assert deleted.status_code == 204
        assert admin_conn.execute(
            "SELECT count(*) FROM git_connections WHERE workspace_id = %s",
            (workspace_id,),
        ).fetchone()[0] == 0
    finally:
        good.close()
        bad.close()


@pytest.mark.asyncio
async def test_lease_sweeper_reclaims_and_resumes(client, admin_conn, monkeypatch):
    from mylife_api import services_lease
    from mylife_api.review_runner import run_review

    async def _no_resume(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(services_lease, "resume_pending_runs", _no_resume)

    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="lease1@example.com", files={}
    )
    run_id = uuid.uuid4()
    admin_conn.execute(
        "INSERT INTO agent_runs "
        "(id, workspace_id, kind, period_key, state, idem_key, trigger_type, "
        " lease_owner, lease_expires_at, state_payload) "
        "VALUES (%s, %s, 'daily', '2026-10-27', 'reviewing', %s, 'manual', "
        "        'dead-worker', now() - interval '1 minute', '{}')",
        (run_id, workspace_id, f"lease:{run_id}"),
    )
    reclaimed = await services_lease.review_sweeper_tick()
    assert reclaimed["reclaimed"] == 1
    row = admin_conn.execute(
        "SELECT state, retry_from_state, lease_owner FROM agent_runs WHERE id = %s",
        (run_id,),
    ).fetchone()
    assert row == ("retry_wait", "reviewing", None)
    events = admin_conn.execute(
        "SELECT count(*) FROM run_events WHERE run_id = %s "
        "AND payload->>'reason' = 'lease_expired'",
        (run_id,),
    ).fetchone()[0]
    assert events == 1
    _ = run_review
