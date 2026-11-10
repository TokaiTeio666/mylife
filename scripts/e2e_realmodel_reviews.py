"""真实模型日/周/年复盘链路 E2E（本机 magpie 反代 + Ollama）。

前置：
- API/worker/dispatcher 容器已启动，且 LLM/Embedding 指向宿主机模型；
- 库中有一个未使用的邀请码。

用法：
    NO_PROXY=localhost,127.0.0.1 uv run python scripts/e2e_realmodel_reviews.py <invite-code>

链路：
1. 注册 → 导入一周自由叙述日记（无「做错的事 / 正确方法论」标题）；
2. 逐日触发 daily 复盘，验证 LLM 语义 Writer 的 writer.mode == "llm"；
3. 触发 monthly 复盘，验证从 daily rollup；
4. 触发 yearly 复盘（默认人工门），审批后交付；
5. 读取 usage_events 确认 writer 用量落在对应 run。

结果写入 `docs/evidence/phase5/realmodel-reviews.json`。
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

import httpx

BASE = "http://localhost:8000/api/v1"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "evidence" / "phase5" / "realmodel-reviews.json"
PG_CONTAINER = "mylife-postgres-1"

TERMINAL = {
    "succeeded",
    "failed",
    "needs_human",
    "human_gate",
    "cancelled",
    "security_abort",
}

DATES = [f"2026-10-{day:02d}" for day in range(5, 12)]
MONTH_KEY = "2026-10"
YEAR_KEY = "2026"

NOTES: dict[str, str] = {
    "2026-10-05": """---
date: 2026-10-05
---
# 10-05 周一

上午准备发布 v0.4 时，我图快直接改了配置就上线，完全没有先备份数据库。
结果迁移脚本把索引名写错，服务起来后查询报错，只能回滚；因为没备份，
重新导入测试数据又花了一个小时。根因是发布清单里根本没有备份这一步，我凭记忆操作。

下次任何会改数据的发布，都要先把备份完成并记录时间，再执行迁移；失败时
直接用备份恢复，不要在现场修数据。
""",
    "2026-10-06": """---
date: 2026-10-06
---
# 10-06 周二

下午排查搜索接口慢的问题，我一开始直接凭感觉把 HNSW 参数从 16 调到 64，
结果延迟反而更高。后来先量了基线 P95，再只改 ef_search 一个变量复测，
才找到瓶颈其实在查询向量重复计算。教训是性能问题必须一次只改一个变量，
否则观测结果没法归因。

以后遇到慢查询，我先记录基线、列出候选原因，然后逐项验证并写下每次复测的数据。
""",
    "2026-10-07": """---
date: 2026-10-07
---
# 10-07 周三

需求评审时我没让产品把验收例子写出来，自己脑补了“导出全部”的含义，
写了三天才发现对方要的是按筛选条件导出，只能返工。这件事的代价是三天的开发时间。

现在开始，我会在动手前把三个典型输入和期望输出写成验收样例，双方确认后再写代码。
""",
    "2026-10-08": """---
date: 2026-10-08
---
# 10-08 周四

昨天上线前我把两个失败的测试注释掉了，想着“跟这次改动无关”，结果其中一个
覆盖了空数据分支，线上真的出现空列表 500。修复 bug 本身只花了二十分钟，
但排查和道歉花了半天。

我给自己定了规矩：修好一个 bug，就补一条能复现它的回归测试；永远不注释
失败测试，要么修，要么明确记录原因并建任务。
""",
    "2026-10-09": """---
date: 2026-10-09
---
# 10-09 周五

今天把新功能放进 feature flag 后面，先对内部账号开放 10%，观察错误率和延迟
都稳定后再逐步放量。这个做法让回滚成本从重新发版降到改一个开关，值得保留。

步骤是：默认关闭、内部白名单、1% → 10% → 50% → 全量，每一档至少观察一天
并记录指标。
""",
    "2026-10-10": """---
date: 2026-10-10
---
# 10-10 周六

读缓存设计资料，理解了布隆过滤器只能判断“一定不存在”，不能判断“一定存在”，
所以它适合挡在缓存前面防穿透，不能替代权限校验。写了三页笔记，但还没动手做实验。

下一步想用一百万随机 key 测一下误判率和内存占用。
""",
    "2026-10-11": """---
date: 2026-10-11
---
# 10-11 周日

这周做研究时没有设时间盒，本来只想看一篇论文，结果顺着引用读了一整天，
原定的月报和复盘都拖到晚上。根因是我没有给探索任务设截止时间。

以后探索类任务先设 45 分钟时间盒，到点必须记录当前结论和下一步，再决定是否继续。
""",
}


def make_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for day, text in NOTES.items():
            archive.writestr(f"日记/2026/{day}.md", text)
    return buffer.getvalue()


def csrf(client: httpx.Client) -> str:
    client.get(f"{BASE}/auth/csrf")
    token = client.cookies.get("mylife_csrf")
    assert token, "csrf cookie missing"
    return token


def post(client: httpx.Client, path: str, payload: dict | None = None):
    token = csrf(client)
    return client.post(
        f"{BASE}{path}",
        json=payload if payload is not None else {},
        headers={"X-CSRF-Token": token},
    )


def poll(fn, *, timeout: float = 300.0, interval: float = 0.5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last is not None:
            return last
        time.sleep(interval)
    raise AssertionError(f"poll timed out; last={last!r}")


def psql(sql: str) -> str:
    proc = subprocess.run(
        [
            "docker",
            "exec",
            PG_CONTAINER,
            "psql",
            "-U",
            "mylife_migrator",
            "-d",
            "mylife",
            "-Atc",
            sql,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def run_state(client: httpx.Client, run_id: str) -> dict | None:
    response = client.get(f"{BASE}/runs/{run_id}")
    if response.status_code != 200:
        return None
    body = response.json()
    if body["state"] in TERMINAL:
        return body
    return None


def run_writer(run_id: str) -> dict:
    raw = psql(
        "SELECT coalesce(state_payload->'writer', '{}'::jsonb)::text "
        f"FROM agent_runs WHERE id = '{run_id}'"
    )
    return json.loads(raw) if raw else {}


def run_usage(run_id: str) -> list[dict]:
    raw = psql(
        "SELECT coalesce(json_agg(json_build_object("
        "  'kind', kind, 'role', role, 'model', model,"
        "  'prompt_tokens', prompt_tokens, 'completion_tokens', completion_tokens,"
        "  'latency_ms', latency_ms"
        ")), '[]'::json)::text "
        f"FROM usage_events WHERE run_id = '{run_id}'"
    )
    return json.loads(raw) if raw else []


def approve(client: httpx.Client, run_id: str) -> None:
    response = post(
        client,
        f"/runs/{run_id}/approve",
        {"decision_note": "真实模型 E2E 人工审批"},
    )
    assert response.status_code in {200, 202}, response.text


def delivery_markdown(client: httpx.Client, review_id: str, kind: str) -> dict:
    response = client.get(f"{BASE}/deliveries?review_id={review_id}")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, "delivery record missing"
    delivery = items[0]
    download = client.get(f"{BASE}/deliveries/{delivery['id']}/download")
    assert download.status_code == 200, download.text
    archive = zipfile.ZipFile(io.BytesIO(download.content))
    folder = {"daily": "每日复盘", "monthly": "每月复盘", "yearly": "年度复盘"}[kind]
    markdown = [
        name
        for name in archive.namelist()
        if name.endswith(".md") and folder in name
    ]
    assert markdown, archive.namelist()
    path = markdown[0]
    return {
        "delivery_id": delivery["id"],
        "target": delivery["target"],
        "status": delivery["status"],
        "degraded": delivery.get("degraded"),
        "path": path,
        "markdown": archive.read(path).decode("utf-8"),
    }


def trigger(client: httpx.Client, kind: str, period_key: str) -> dict:
    created = post(
        client,
        "/reviews",
        {"kind": kind, "period_key": period_key, "allow_empty": True},
    )
    assert created.status_code in {200, 201}, created.text
    body = created.json()
    run_id = body["run_id"]
    states: list[str] = []

    def done():
        response = client.get(f"{BASE}/runs/{run_id}")
        if response.status_code != 200:
            return None
        run = response.json()
        if not states or states[-1] != run["state"]:
            states.append(run["state"])
        return run if run["state"] in TERMINAL else None

    run = poll(done, timeout=420)
    approved = False
    if run["state"] == "human_gate":
        approve(client, run_id)
        approved = True
        run = poll(done, timeout=300)

    review_id = body.get("review_id") or run.get("review_id")
    assert run["state"] == "succeeded", (kind, period_key, run)
    detail = client.get(f"{BASE}/reviews/{review_id}")
    assert detail.status_code == 200, detail.text
    review = detail.json()
    delivered = delivery_markdown(client, str(review_id), kind)
    return {
        "kind": kind,
        "period_key": period_key,
        "run_id": run_id,
        "states": states,
        "approved_human_gate": approved,
        "final_state": run["state"],
        "degraded": run.get("degraded"),
        "degraded_reasons": run.get("degraded_reasons"),
        "writer": run_writer(run_id),
        "usage": run_usage(run_id),
        "sections": [section["section_key"] for section in review["sections"]],
        "entries": len(review.get("entries") or []),
        "delivery": {
            "target": delivered["target"],
            "status": delivered["status"],
            "path": delivered["path"],
            "markdown_bytes": len(delivered["markdown"].encode("utf-8")),
        },
        "markdown_preview": delivered["markdown"][:1500],
    }


def main(invite: str) -> int:
    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": BASE,
        "model_note": "LLM 走本机 magpie 反代，Embedding 走本机 Ollama；容器内用 host.docker.internal。",
    }
    with httpx.Client(verify=False, timeout=600.0, trust_env=False) as client:
        email = f"realmodel-reviews-{uuid.uuid4().hex[:8]}@example.com"
        registered = post(
            client,
            "/auth/register",
            {
                "email": email,
                "password": "Realmodel" + "-Review-2026",
                "display_name": "Real Model Reviews",
                "invite_code": invite,
            },
        )
        assert registered.status_code == 201, registered.text
        workspace_id = registered.json()["workspace"]["id"]
        report["workspace_id"] = workspace_id
        report["email"] = email

        token = csrf(client)
        upload = client.post(
            f"{BASE}/ingest/zip",
            files={"file": ("week.zip", make_zip(), "application/zip")},
            data={"mode": "managed", "reviews_root": "wiki/复盘"},
            headers={"X-CSRF-Token": token},
        )
        assert upload.status_code == 202, upload.text
        job_id = upload.json()["job"]["id"]

        def ingest_done():
            response = client.get(f"{BASE}/ingest/jobs/{job_id}")
            if response.status_code != 200:
                return None
            detail = response.json()
            return (
                detail
                if detail["job"]["status"] in {"succeeded", "partial", "failed"}
                else None
            )

        ingest = poll(ingest_done, timeout=420)
        assert ingest["job"]["status"] == "succeeded", ingest
        report["ingest"] = {
            "status": ingest["job"]["status"],
            "counters": ingest.get("counters"),
            "failed_items": ingest.get("failed_items"),
        }

        report["daily"] = []
        for day in DATES:
            print(f"daily {day} ...", flush=True)
            row = trigger(client, "daily", day)
            report["daily"].append(row)
            print(
                f"  state={row['final_state']} writer={row['writer'].get('mode')} "
                f"mistakes={row['writer'].get('mistakes')} "
                f"methods={row['writer'].get('methods')} entries={row['entries']}",
                flush=True,
            )

        print(f"monthly {MONTH_KEY} ...", flush=True)
        report["monthly"] = trigger(client, "monthly", MONTH_KEY)
        print(
            f"  state={report['monthly']['final_state']} "
            f"approved={report['monthly']['approved_human_gate']} "
            f"entries={report['monthly']['entries']}",
            flush=True,
        )

        print(f"yearly {YEAR_KEY} ...", flush=True)
        report["yearly"] = trigger(client, "yearly", YEAR_KEY)
        print(
            f"  state={report['yearly']['final_state']} "
            f"approved={report['yearly']['approved_human_gate']} "
            f"entries={report['yearly']['entries']}",
            flush=True,
        )

        llm_days = [
            row["period_key"]
            for row in report["daily"]
            if row["writer"].get("mode") == "llm"
        ]
        report["summary"] = {
            "daily_days": len(report["daily"]),
            "daily_llm_days": llm_days,
            "writer_usage_runs": sum(
                1
                for row in report["daily"]
                if any(item.get("role") == "writer" for item in row["usage"])
            ),
            "monthly_ok": report["monthly"]["final_state"] == "succeeded",
            "yearly_ok": report["yearly"]["final_state"] == "succeeded",
        }
        report["findings"] = {
            "period_change": "weekly 已替换为 monthly：周期键 YYYY-MM，每月 1 日生成上月，章节 next_month_actions。",
            "daily_writer": "日复盘使用配置的 LLM 做语义抽取；writer.mode=llm 表示模型结果被采用。",
            "monthly_rollup": "月复盘从 daily review_entries/sections rollup；方法论落地来自 daily methods。",
            "yearly_rollup": "年复盘学习地图读 monthly sections；Top10 错误/方法论体系读全年 daily review_entries。",
        }
        assert len(llm_days) == len(DATES), report["summary"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: e2e_realmodel_reviews.py <invite-code>")
    raise SystemExit(main(sys.argv[1]))
