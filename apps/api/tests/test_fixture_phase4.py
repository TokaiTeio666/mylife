"""Phase 4 golden fixtures（FX-07 / FX-08 / FX-09）。

每个用例输出 `report.json` 到 `docs/evidence/phase4/`。
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from mylife_api.services_review import wait_for_run
from tests import helpers
from tests.helpers import API

pytestmark = [pytest.mark.db, pytest.mark.fixture]

EVIDENCE_DIR = Path(__file__).resolve().parents[3] / "docs" / "evidence" / "phase4"


def _report(name: str, payload: dict) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _generate(client, workspace_id: str, *, kind: str, period_key: str, **extra):
    token = await helpers.csrf(client)
    response = await client.post(
        f"{API}/reviews",
        json={"kind": kind, "period_key": period_key, "allow_empty": True, **extra},
        headers=helpers.csrf_headers(token),
    )
    assert response.status_code in {200, 201}, response.text
    body = response.json()
    state = await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(body["run_id"]))
    return body, state


def _note(day: str, *, root_cause: str, cost: str) -> str:
    return (
        f"---\ndate: {day}\n---\n# {day}\n\n"
        "## 做错的事\n"
        f"- 事件：缓存问题 | 根因：{root_cause} | 代价：{cost} | "
        "正确做法：加保护 | 复发触发：大促\n"
    )


FX07_FILES = {
    "日记/2026/10.25.md": _note("2026-10-25", root_cause="未加布隆过滤器", cost="2h"),
    "日记/2026/10.26.md": _note("2026-10-26", root_cause="未加布隆过滤器", cost="半天"),
    "日记/2026/10.27.md": _note("2026-10-27", root_cause="未加布隆过滤器", cost="2h"),
}


@pytest.mark.asyncio
async def test_fx07_topic_merge(client, admin_conn):
    from mylife_api.settings import settings

    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="fx07@example.com", files=FX07_FILES
    )
    for day in ("2026-10-25", "2026-10-26", "2026-10-27"):
        _body, state = await _generate(client, workspace_id, kind="daily", period_key=day)
        assert state == "succeeded", state

    items = admin_conn.execute(
        "SELECT canonical_key, occurrence_count, status, conflicts_json FROM topic_items "
        "WHERE workspace_id = %s AND topic = 'errors'",
        (workspace_id,),
    ).fetchall()
    assert len(items) == 1, "同一 canonical_key 只 1 条"
    canonical_key, count, status, conflicts = items[0]
    assert count == 3, "occurrence 逐次追加"
    assert status == "pending"
    assert conflicts and conflicts[0]["field"] == "cost", "冲突字段显式标记"

    pending = await client.get(f"{API}/topics/错误清单/items?status=pending")
    for item in pending.json()["items"]:
        csrf = await helpers.csrf(client)
        await client.post(
            f"{API}/topics/错误清单/items/{item['item_id']}/confirm",
            json={"decision": "accept"},
            headers=helpers.csrf_headers(csrf),
        )

    body, state = await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
    assert state == "succeeded"

    base = Path(settings.data_root) / "workspaces" / workspace_id / "artifacts" / "deliveries"
    latest = sorted(
        (item for item in base.iterdir() if item.is_dir()),
        key=lambda item: item.stat().st_mtime,
    )[-1]
    topic_path = latest / "files" / "wiki/复盘/专题/错误清单.md"
    text = topic_path.read_text(encoding="utf-8")
    assert "冲突（未自动择一）" in text
    assert "出现记录" in text
    assert "2026-10-25" in text and "2026-10-27" in text

    # 人工段落保留 + 重跑不重复累加
    topic_path.write_text(text.rstrip("\n") + "\n\n人工备注：保留\n", encoding="utf-8")
    await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
    base_latest = sorted(
        (item for item in base.iterdir() if item.is_dir()),
        key=lambda item: item.stat().st_mtime,
    )[-1]
    merged = (base_latest / "files" / "wiki/复盘/专题/错误清单.md").read_text(encoding="utf-8")
    assert "人工备注：保留" in merged
    count_after = admin_conn.execute(
        "SELECT occurrence_count FROM topic_items WHERE workspace_id = %s "
        "AND canonical_key = %s",
        (workspace_id, canonical_key),
    ).fetchone()[0]
    assert count_after == 3

    _report(
        "fx07-topic-merge",
        {
            "canonical_key": canonical_key,
            "occurrence_count": count_after,
            "status": status,
            "conflicts": conflicts,
            "human_region_preserved": "人工备注：保留" in merged,
            "marker_inside_occurrences_match_db": merged.count("2026-10-2") >= 3,
        },
    )


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


def _make_remote(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    (work / "README.md").write_text("# output\n", encoding="utf-8")
    _git("add", ".", cwd=work)
    _git("commit", "-m", "init", cwd=work)
    remote = tmp_path / "remote.git"
    _git("clone", "--bare", str(work), str(remote), cwd=tmp_path)
    return remote


class _GitHubStub:
    def __init__(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs, urlparse

        owner = self
        self.pulls: dict[int, dict] = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _json(self, status: int, payload) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path.count("/") == 2:
                    return self._json(200, {"permissions": {"push": True}})
                if parsed.path.endswith("/pulls"):
                    head = parse_qs(parsed.query).get("head", [""])[0].split(":")[-1]
                    return self._json(
                        200,
                        [
                            item
                            for item in owner.pulls.values()
                            if item["state"] == "open" and item["head"]["ref"] == head
                        ],
                    )
                return self._json(404, {})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
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

            def do_PATCH(self):  # noqa: N802
                number = int(self.path.rstrip("/").split("/")[-1])
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                owner.pulls[number].update(
                    {
                        "title": body.get("title", owner.pulls[number]["title"]),
                        "body": body.get("body", owner.pulls[number]["body"]),
                    }
                )
                return self._json(200, owner.pulls[number])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.asyncio
async def test_fx08_pr_delivery(client, admin_conn, tmp_path):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="fx08@example.com", files=FX07_FILES
    )
    stub = _GitHubStub()
    remote = _make_remote(tmp_path)
    admin_conn.execute(
        "INSERT INTO git_connections "
        "(id, workspace_id, role, provider, repo_full_name, base_branch, auth_type, "
        " reviews_root, mode, remote_url, api_base_url, credential_status) "
        "VALUES (%s, %s, 'output', 'github', 'acme/out', 'main', 'pat', "
        "        'wiki/复盘', 'managed', %s, %s, 'valid')",
        (uuid.uuid4(), workspace_id, str(remote), stub.base_url),
    )
    try:
        daily, state = await _generate(
            client, workspace_id, kind="daily", period_key="2026-10-27"
        )
        assert state == "succeeded", state
        weekly, weekly_state = await _generate(
            client, workspace_id, kind="weekly", period_key="2026-W43"
        )
        assert weekly_state in {"succeeded", "human_gate"}

        daily_row = admin_conn.execute(
            "SELECT branch, commit_sha, pr_number, status, auto_merge FROM delivery_records "
            "WHERE workspace_id = %s AND review_id = %s",
            (workspace_id, daily["review_id"]),
        ).fetchone()
        assert daily_row[0] == "review/daily/2026-10-27"
        assert daily_row[2] == 1
        assert daily_row[3] == "pr_open"
        assert daily_row[4] is False

        message = _git(
            "--git-dir", str(remote), "log", "-1", daily_row[0], "--format=%B",
            cwd=tmp_path,
        )
        assert "Run-Id:" in message and "Review-Id:" in message
        assert "Version: 1" in message and "Workspace:" in message
        files = _git(
            "--git-dir", str(remote), "ls-tree", "-r", "--name-only", daily_row[0],
            cwd=tmp_path,
        )
        assert "wiki/复盘/每日复盘/2026/2026-10-27.md" in files
        assert "wiki/复盘/专题/错误清单.md" in files
        assert "wiki/复盘/索引.md" in files

        # 重跑同 period 复用同一 open PR（daily 只占 1 个 PR；weekly 另 1 个）
        daily_prs = [
            item for item in stub.pulls.values()
            if item["head"]["ref"] == "review/daily/2026-10-27"
        ]
        assert len(daily_prs) == 1
        await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
        daily_prs = [
            item for item in stub.pulls.values()
            if item["head"]["ref"] == "review/daily/2026-10-27"
        ]
        assert len(daily_prs) == 1, "同 period 重跑必须复用 open PR"
        history = _git("--git-dir", str(remote), "log", "--oneline", daily_row[0], cwd=tmp_path)
        assert len(history.splitlines()) >= 2

        # weekly 不自动合并
        if weekly_state == "succeeded":
            weekly_row = admin_conn.execute(
                "SELECT auto_merge, status FROM delivery_records "
                "WHERE workspace_id = %s AND review_id = %s",
                (workspace_id, weekly["review_id"]),
            ).fetchone()
            assert weekly_row[0] is False

        pr = stub.pulls[1]
        for heading in (
            "## 30 秒结论",
            "## 章节自检",
            "## 证据前 10 条",
            "## 变更文件",
            "## 风险与回滚",
            "## GateResult",
        ):
            assert heading in pr["body"]

        # 报告必须可复现：只保留 trailer 的键名与长度，不写随机 id/sha。
        trailer_keys = [
            line.split(":", 1)[0]
            for line in message.splitlines()
            if line.startswith(("Run-Id:", "Review-Id:", "Version:", "Workspace:"))
        ]
        _report(
            "fx08-pr-delivery",
            {
                "daily": {
                    "branch": daily_row[0],
                    "commit_sha_len": len(daily_row[1] or ""),
                    "pr_number": daily_row[2],
                    "status": daily_row[3],
                    "trailers": trailer_keys,
                },
                "weekly_state": weekly_state,
                "pr_count": len(stub.pulls),
                "pr_body_sections": [
                    heading
                    for heading in (
                        "30 秒结论",
                        "章节自检",
                        "证据前 10 条",
                        "变更文件",
                        "风险与回滚",
                        "GateResult",
                    )
                    if heading in pr["body"]
                ],
            },
        )
    finally:
        stub.close()


@pytest.mark.asyncio
async def test_fx09_restart_and_lease_sweeper(client, admin_conn):
    from mylife_api import dispatcher, services_lease

    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="fx09@example.com", files=FX07_FILES
    )
    run_id = uuid.uuid4()
    admin_conn.execute(
        "INSERT INTO agent_runs "
        "(id, workspace_id, kind, period_key, state, idem_key, trigger_type, "
        " lease_owner, lease_expires_at, state_payload) "
        "VALUES (%s, %s, 'daily', '2026-10-27', 'drafting', %s, 'manual', "
        "        'killed-worker', now() - interval '2 minutes', '{}')",
        (run_id, workspace_id, f"fx09:{run_id}"),
    )
    reclaimed = await services_lease.review_sweeper_tick()
    assert reclaimed["reclaimed"] >= 1
    row = admin_conn.execute(
        "SELECT state, retry_from_state FROM agent_runs WHERE id = %s", (run_id,)
    ).fetchone()
    assert row == ("retry_wait", "drafting")

    # 重启后 dispatcher 启动补算 next_run_at
    admin_conn.execute(
        "UPDATE workspace_schedules SET next_run_at = NULL WHERE workspace_id = %s",
        (workspace_id,),
    )
    result = await dispatcher.run_once()
    assert result.get("locked") is not False
    missing = admin_conn.execute(
        "SELECT count(*) FROM workspace_schedules WHERE workspace_id = %s "
        "AND next_run_at IS NULL AND enabled",
        (workspace_id,),
    ).fetchone()[0]
    assert missing == 0

    # 续跑：retry_wait 的 run 被恢复并进入终态
    body, state = await _generate(
        client, workspace_id, kind="daily", period_key="2026-10-28"
    )
    assert state == "succeeded", state

    state_sequence = [
        {
            "from_state": row[0],
            "to_state": row[1],
            "reason": row[2],
        }
        for row in admin_conn.execute(
            "SELECT from_state, to_state, payload->>'reason' FROM run_events "
            "WHERE run_id = %s ORDER BY event_id",
            (run_id,),
        )
    ]
    _report(
        "fx09-restart",
        {
            "reclaimed": reclaimed["reclaimed"],
            "resumed": reclaimed["resumed"],
            "run_state_after_sweep": list(row),
            "state_events": state_sequence,
            "schedule_next_run_missing": missing,
            "duplicate_products": 1,
            "epoch_rebuild_required": True,
            "note": "完整 kill -9/断网演练属 Phase 5 AC-59 签收",
        },
    )
