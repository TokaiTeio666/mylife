"""Phase 4 容器 E2E（经 Caddy HTTPS）。

用法（先在本机库中插入一个未使用邀请码）：

    uv run python scripts/e2e_phase4.py <invite-code>

流程：注册 → 导入 ZIP → 生成 daily 复盘 → 交付（对象存储 + ZIP 下载）→
专题 pending/confirm → 仪表盘 → 搜索三模式 → 调度设置 → 删除冷却。
结果写入 `docs/evidence/phase4/e2e-container.json`。
"""

from __future__ import annotations

import io
import json
import sys
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlencode

import httpx

BASE = "https://localhost/api/v1"
OUT = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "phase4" / "e2e-container.json"

NOTE = """---
date: 2026-10-27
---
# 10-27

## 做错的事
- 事件：缓存问题 | 根因：未加布隆过滤器 | 代价：2h | 正确做法：加保护 | 复发触发：大促

## 正确方法论
- 名称：布隆过滤器 | 来源：实践 | 场景：缓存 | 步骤：估算容量;分层校验 | 边界：删除场景 | 落地：本周接入 | 验证：压测通过
"""


def make_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("日记/2026/10.27.md", NOTE)
        archive.writestr("notes/cache.md", "---\ndate: 2026-10-27\n---\n# 缓存\n缓存穿透。\n")
    return buffer.getvalue()


def csrf(client: httpx.Client) -> str:
    client.get(f"{BASE}/auth/csrf")
    token = client.cookies.get("mylife_csrf")
    assert token, "csrf cookie missing"
    return token


def post(client: httpx.Client, path: str, payload: dict | None = None, **kwargs):
    token = csrf(client)
    return client.post(
        f"{BASE}{path}",
        json=payload if payload is not None else {},
        headers={"X-CSRF-Token": token},
        **kwargs,
    )


def main(invite: str) -> int:
    report: dict = {"steps": []}
    with httpx.Client(verify=False, timeout=120.0) as client:
        email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
        registered = post(
            client,
            "/auth/register",
            {
                "email": email,
                "password": "correct-horse-9",
                "display_name": "E2E",
                "invite_code": invite,
            },
        )
        assert registered.status_code == 201, registered.text
        workspace_id = registered.json()["workspace"]["id"]
        report["workspace_id"] = workspace_id
        report["steps"].append({"step": "register", "status": registered.status_code})

        token = csrf(client)
        upload = client.post(
            f"{BASE}/ingest/zip",
            files={"file": ("vault.zip", make_zip(), "application/zip")},
            data={"mode": "managed", "reviews_root": "wiki/复盘"},
            headers={"X-CSRF-Token": token},
        )
        assert upload.status_code == 202, upload.text
        job_id = upload.json()["job"]["id"]
        deadline = time.time() + 180
        job_status = "queued"
        detail: dict = {}
        while time.time() < deadline:
            detail = client.get(f"{BASE}/ingest/jobs/{job_id}").json()
            job_status = detail["job"]["status"]
            if job_status in {"succeeded", "partial", "failed"}:
                break
            time.sleep(1)
        assert job_status in {"succeeded", "partial"}, detail
        report["steps"].append({"step": "ingest", "status": job_status})

        created = post(
            client,
            "/reviews",
            {"kind": "daily", "period_key": "2026-10-27", "allow_empty": True},
        )
        assert created.status_code in {200, 201}, created.text
        run_id = created.json()["run_id"]
        review_id = created.json()["review_id"]
        state = "created"
        run: dict = {}
        deadline = time.time() + 180
        while time.time() < deadline:
            run = client.get(f"{BASE}/runs/{run_id}").json()
            state = run["state"]
            if state in {"succeeded", "failed", "needs_human", "cancelled", "security_abort"}:
                break
            time.sleep(1)
        report["steps"].append({"step": "review", "run_id": run_id, "state": state})
        assert state == "succeeded", run

        review = client.get(f"{BASE}/reviews/{review_id}").json()
        report["review"] = {
            "version": review["version"],
            "sections": [section["section_key"] for section in review["sections"]],
            "degraded": review["degraded"],
        }
        assert len(review["sections"]) == 7

        deliveries = client.get(f"{BASE}/deliveries?review_id={review_id}").json()["items"]
        assert deliveries, "delivery record missing"
        delivery = deliveries[0]
        report["delivery"] = {
            "id": delivery["id"],
            "target": delivery["target"],
            "status": delivery["status"],
            "degraded": delivery["degraded"],
        }
        manifest = client.get(f"{BASE}/deliveries/{delivery['id']}").json()["manifest"]
        report["delivery"]["manifest_paths"] = [item["path"] for item in manifest["targets"]]
        download = client.get(f"{BASE}/deliveries/{delivery['id']}/download")
        report["delivery"]["download_status"] = download.status_code
        assert download.status_code == 200

        topics = client.get(f"{BASE}/topics").json()["items"]
        report["topics"] = [
            {"topic": item["topic"], "open_count": item["open_count"]} for item in topics
        ]

        pending = client.get(f"{BASE}/topics/错误清单/items?status=pending").json()["items"]
        confirmed = 0
        for item in pending:
            token = csrf(client)
            response = client.post(
                f"{BASE}/topics/错误清单/items/{item['item_id']}/confirm",
                json={"decision": "accept"},
                headers={"X-CSRF-Token": token},
            )
            confirmed += 1 if response.status_code == 200 else 0
        report["topics_pending"] = len(pending)
        report["topics_confirmed"] = confirmed

        latest = client.get(f"{BASE}/dashboard/latest").json()
        report["dashboard"] = {
            "daily": latest["daily"]["period_key"] if latest["daily"] else None,
            "completion_rate": latest["completion_rate"],
            "open_tasks": latest["open_tasks"],
        }
        calendar = client.get(f"{BASE}/dashboard/calendar?year=2026&month=10").json()
        report["calendar_days"] = len(calendar["days"])

        both = client.get(
            f"{BASE}/search?{urlencode({'q': '缓存', 'source_type': 'both'})}"
        ).json()
        report["search_sources"] = sorted({item["source_type"] for item in both["items"]})
        only_review = client.get(
            f"{BASE}/search?{urlencode({'q': '缓存', 'source_type': 'review'})}"
        ).json()
        report["search_review_count"] = len(only_review["items"])

        schedule = client.get(f"{BASE}/settings/schedule").json()
        report["schedule"] = {
            "timezone": schedule["timezone"],
            "daily_at": schedule["daily_at"],
            "next_run_at": schedule["next_run_at"],
        }
        token = csrf(client)
        updated = client.put(
            f"{BASE}/settings/schedule",
            json={
                "timezone": "Asia/Shanghai",
                "daily_at": "02:00",
                "weekly_at": "02:30",
                "yearly_at": "03:00",
                "catchup_days": 7,
                "enabled": {"daily": True, "weekly": True, "yearly": True},
            },
            headers={"X-CSRF-Token": token},
        )
        report["schedule_update"] = updated.status_code
        assert updated.status_code == 200

        workspace = client.get(f"{BASE}/workspaces/{workspace_id}").json()
        name = workspace["workspace"]["name"]
        deletion = post(client, "/deletions", {"scope": "workspace", "confirm_text": name})
        report["deletion"] = {"status": deletion.status_code}
        assert deletion.status_code == 201, deletion.text
        report["deletion"]["state"] = deletion.json()["state"]
        report["deletion"]["notice_retain"] = deletion.json()["notice"]["will_retain"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: e2e_phase4.py <invite-code>")
    raise SystemExit(main(sys.argv[1]))
