"""真实模型链路 E2E：复盘生成 + 门禁交付、Embedding 蓝绿换维。

前置：
- `api/worker` 容器已接到真实 provider（本机 magpie 反代 + Ollama，见 `.env`）；
- 库中有一个未使用的邀请码。

用法：
    NO_PROXY=localhost,127.0.0.1 uv run python scripts/e2e_realmodel.py <invite-code>

结果写入 `docs/evidence/phase5/realmodel-chains.json`。

注意：复盘 Writer 自 Phase 3 起是确定性装配器（IMPLEMENTATION-NOTES §2C P3-2），
本链路验证的是「导入（真实 embedding）→ 复盘 FSM → G0–G9 → 对象存储交付」，
不会调用 chat LLM；真实 LLM 只在 chat 链路中使用。蓝绿换维则完整调用真实
embedding 模型（1024 维 qwen3-embedding → 768 维 nomic-embed-text）。
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
from urllib.parse import urlencode

import httpx

BASE = "https://localhost/api/v1"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "evidence" / "phase5" / "realmodel-chains.json"
CONTAINER = "mylife-api-1"
PG_CONTAINER = "mylife-postgres-1"

NOTE_2026_11_09 = """---
date: 2026-11-09
---
# 11-09

- [x] 修复向量检索超时
- [ ] 补写 HNSW 压测

## 做错的事
- 事件：查询向量未缓存 | 根因：每次请求都重新 embedding | 代价：3h | 正确做法：加 256 条 LRU | 复发触发：高频重试

## 正确方法论
- 名称：先量化再优化 | 来源：实践 | 场景：检索性能 | 步骤：测基线;改一处;复测 | 边界：小数据量 | 落地：本月 | 验证：P95 下降
"""

NOTE_VECTOR = """---
date: 2026-11-09
---
# 向量检索

HNSW 语句超时固定为 250 毫秒；查询向量使用 256 条 LRU 缓存，
避免重复调用 embedding 服务。自然语言问题走 RRF 融合。
"""

NOTE_YESTERDAY = """---
date: 2026-11-08
---
# 11-08

- [ ] 对比 BGE-M3 与 nomic-embed-text 的召回
- [ ] 检查 delivery manifest

昨天梳理了缓存穿透与布隆过滤器的适用边界。
"""


def make_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("日记/2026/11.09.md", NOTE_2026_11_09)
        archive.writestr("日记/2026/11.08.md", NOTE_YESTERDAY)
        archive.writestr("学习/向量检索.md", NOTE_VECTOR)
        archive.writestr(
            "学习/缓存.md",
            "---\ndate: 2026-11-08\n---\n# 缓存\n缓存穿透可以先用布隆过滤器拦截。\n",
        )
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


def poll(fn, *, timeout: float = 240.0, interval: float = 1.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last is not None:
            return last
        time.sleep(interval)
    raise AssertionError(f"poll timed out; last={last!r}")


def embedding_state(client: httpx.Client) -> dict:
    response = client.get(f"{BASE}/settings/embedding")
    assert response.status_code == 200, response.text
    return response.json()


def find_profile(state: dict, profile_id: str) -> dict:
    for profile in state.get("profiles") or []:
        if str(profile["id"]) == str(profile_id):
            return profile
    raise AssertionError(f"profile {profile_id} missing from {state}")


def wait_reembed(client: httpx.Client, profile_id: str) -> dict:
    def check():
        state = embedding_state(client)
        profile = find_profile(state, profile_id)
        reembed = state.get("reembed") or {}
        if reembed.get("status") in {"succeeded", "failed"}:
            return {"state": state, "profile": profile, "reembed": reembed}
        return None

    try:
        return poll(check, timeout=300, interval=0.5)
    except AssertionError:
        state = embedding_state(client)
        raise AssertionError(
            f"reembed did not finish: {json.dumps(state, ensure_ascii=False)}"
        )


def search(
    client: httpx.Client, query: str, *, mode: str = "vector", top_k: int = 5
) -> dict:
    response = client.get(
        f"{BASE}/search?{urlencode({'q': query, 'mode': mode, 'top_k': top_k})}"
    )
    assert response.status_code == 200, response.text
    return response.json()


def search_with_retry(
    client: httpx.Client, query: str, *, mode: str = "vector", attempts: int = 3
) -> tuple[dict, list[dict]]:
    """模型刚被换入/冷启动时首次查询可能超时；重试并记录每次结果。"""
    seen: list[dict] = []
    body: dict = {}
    for _ in range(attempts):
        body = search(client, query, mode=mode)
        seen.append(
            {
                "took_ms": body["took_ms"],
                "degraded": body["degraded"],
                "degraded_reasons": body["degraded_reasons"],
                "items": len(body["items"]),
            }
        )
        if body["items"]:
            break
        time.sleep(1.0)
    return body, seen


def activate_profile(workspace_id: str, profile_id: str) -> dict:
    """激活 profile 目前没有 HTTP 端点（见证据说明），按服务层调用链验证。"""
    script = f"""
import asyncio
from uuid import UUID
from mylife_api.db import get_session_factory, set_rls_context
from mylife_api import services_embedding

async def main():
    factory = get_session_factory()
    async with factory() as session:
        await set_rls_context(session, UUID({workspace_id!r}), None)
        result = await services_embedding.activate_profile(
            session,
            workspace_id=UUID({workspace_id!r}),
            profile_id=UUID({profile_id!r}),
        )
        print("ACTIVATE_RESULT " + repr(result))

asyncio.run(main())
"""
    proc = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "uv", "run", "--no-sync", "python", "-"],
        input=script,
        capture_output=True,
        text=True,
        timeout=180,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"activation failed: {proc.stderr}"
    for line in proc.stdout.splitlines():
        if line.startswith("ACTIVATE_RESULT "):
            return {"output": line.removeprefix("ACTIVATE_RESULT ")}
    raise AssertionError(f"activation output missing: {proc.stdout}")


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


def chunk_stats(workspace_id: str, table: str) -> dict:
    row = psql(
        "SELECT count(*), count(embedding), "
        "       coalesce(min(vector_dims(embedding)), 0) "
        f"FROM {table} WHERE workspace_id = '{workspace_id}'"
    )
    total, embedded, dims = (row.split("|") + ["0", "0", "0"])[:3]
    return {
        "table": table,
        "rows": int(total),
        "embedded": int(embedded),
        "dims": int(dims),
    }


def warm_ollama(model: str) -> dict:
    """预热本地 embedding 模型。

    生产 provider 的 HTTP 超时是 10s；Ollama 冷启动加载模型可能超过该值，
    首次调用会得到 EMBEDDING_UNAVAILABLE。测试脚本先用长超时直连预热。
    """
    with httpx.Client(trust_env=False, timeout=180.0) as client:
        response = client.post(
            "http://127.0.0.1:11434/v1/embeddings",
            json={"model": model, "input": ["warmup"]},
        )
        return {
            "model": model,
            "status": response.status_code,
            "dimension": len(response.json()["data"][0]["embedding"])
            if response.status_code == 200
            else None,
        }


def main(invite: str) -> int:
    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "notes": [
            "复盘 Writer 为确定性装配（IMPLEMENTATION-NOTES P3-2），该链路不含 LLM 调用；导入与检索使用真实 embedding。",
            "蓝绿换维的激活步骤没有 HTTP 端点，按 services_embedding.activate_profile 调用链验证。",
            "运行镜像必须带 --build：旧镜像缺少 cdb06a8 的 search_embedding_timeout_ms，真实 embedding 首次调用会 vector_timeout。",
            "Ollama 冷加载 768 维模型可能超过 provider 的 10s HTTP 超时，re-embed 首次会失败且不自动重试；脚本先直连预热。",
            "/settings/embedding/test 按环境维度 1024 校验返回向量，测试 768 维模型会得到 EMBEDDING_UNAVAILABLE。",
        ],
    }
    with httpx.Client(verify=False, timeout=180.0, trust_env=False) as client:
        email = f"realmodel-{uuid.uuid4().hex[:8]}@example.com"
        registered = post(
            client,
            "/auth/register",
            {
                "email": email,
                "password": "correct-horse-9",
                "display_name": "Real Model E2E",
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
            files={"file": ("vault.zip", make_zip(), "application/zip")},
            data={"mode": "managed", "reviews_root": "wiki/复盘"},
            headers={"X-CSRF-Token": token},
        )
        assert upload.status_code == 202, upload.text
        job_id = upload.json()["job"]["id"]

        def ingest_done():
            response = client.get(f"{BASE}/ingest/jobs/{job_id}")
            # 上传返回后立刻查询可能短暂 404（提交可见性竞态）；继续轮询即可。
            if response.status_code != 200:
                return None
            detail = response.json()
            status = detail["job"]["status"]
            return detail if status in {"succeeded", "partial", "failed"} else None

        ingest = poll(ingest_done, timeout=300)
        assert ingest["job"]["status"] == "succeeded", ingest
        report["ingest"] = {
            "status": ingest["job"]["status"],
            "progress": ingest["job"].get("progress"),
            "counters": ingest.get("counters"),
            "snapshot": ingest.get("snapshot"),
            "failed_items": ingest.get("failed_items"),
        }

        # 真实 embedding 连通性：返回观测维度说明请求确实落到了模型。
        # 先直连预热：模型的冷加载时间可能超过 provider 内建的 10s 超时。
        report["model_warmup"] = [
            warm_ollama("qwen3-embedding:0.6b"),
            warm_ollama("nomic-embed-text"),
        ]
        real_emb = post(
            client,
            "/settings/embedding/test",
            {"provider": "openai", "model": "qwen3-embedding:0.6b", "api_key": "local"},
        )
        report["embedding_test_1024"] = {
            "status": real_emb.status_code,
            "body": real_emb.json() if real_emb.status_code == 200 else real_emb.text,
        }
        assert real_emb.status_code == 200, real_emb.text
        assert real_emb.json()["ok"] is True, real_emb.json()
        assert real_emb.json()["dimension_observed"] == 1024, real_emb.json()

        # `/settings/embedding/test` 固定按环境维度（1024）校验返回向量，
        # 因此用它测 768 维模型会得到 EMBEDDING_UNAVAILABLE；这是测试端点的
        # 已知限制，768 维以 re-embed 后的表结构为准，这里只记录观测结果。
        real_emb_768 = post(
            client,
            "/settings/embedding/test",
            {"provider": "openai", "model": "nomic-embed-text", "api_key": "local"},
        )
        report["embedding_test_768"] = {
            "status": real_emb_768.status_code,
            "body": real_emb_768.json()
            if real_emb_768.status_code == 200
            else real_emb_768.text,
            "note": "测试端点按环境维度 1024 校验，故 768 维模型会报 EMBEDDING_UNAVAILABLE",
        }

        before_search, before_attempts = search_with_retry(
            client, "HNSW 语句超时与查询向量缓存"
        )
        report["search_before_switch"] = {
            "matched_by": [item["matched_by"] for item in before_search["items"]],
            "paths": [item["path"] for item in before_search["items"]],
            "degraded": before_search["degraded"],
            "degraded_reasons": before_search["degraded_reasons"],
            "attempts": before_attempts,
        }
        assert before_search["items"], (
            f"vector search returned no items: {before_attempts}"
        )

        # ------------------------------------------------------------------
        # 链路 A：复盘生成 → 门禁 → 交付
        # ------------------------------------------------------------------
        created = post(
            client,
            "/reviews",
            {"kind": "daily", "period_key": "2026-11-09", "allow_empty": True},
        )
        assert created.status_code in {200, 201}, created.text
        run_id = created.json()["run_id"]
        review_id = created.json()["review_id"]

        seen_states: list[str] = []

        def run_done():
            response = client.get(f"{BASE}/runs/{run_id}")
            if response.status_code != 200:
                return None
            run = response.json()
            state = run["state"]
            if not seen_states or seen_states[-1] != state:
                seen_states.append(state)
            if state in {
                "succeeded",
                "failed",
                "needs_human",
                "cancelled",
                "security_abort",
            }:
                return run
            return None

        run = poll(run_done, timeout=300, interval=0.5)
        report["review_run"] = {
            "run_id": run_id,
            "review_id": review_id,
            "states": seen_states,
            "final_state": run["state"],
            "degraded": run.get("degraded"),
        }
        assert run["state"] == "succeeded", run

        review = client.get(f"{BASE}/reviews/{review_id}").json()
        report["review"] = {
            "version": review["version"],
            "status": review.get("status"),
            "sections": [section["section_key"] for section in review["sections"]],
            "verdict_decision": (review.get("verdict") or {}).get("decision"),
            "blocking": len((review.get("verdict") or {}).get("blocking") or []),
            "degraded": review.get("degraded"),
        }
        assert len(review["sections"]) == 7, review["sections"]

        deliveries = client.get(f"{BASE}/deliveries?review_id={review_id}").json()[
            "items"
        ]
        assert deliveries, "delivery record missing"
        delivery = deliveries[0]
        manifest = client.get(f"{BASE}/deliveries/{delivery['id']}").json()["manifest"]
        download = client.get(f"{BASE}/deliveries/{delivery['id']}/download")
        assert download.status_code == 200, download.text
        archive = zipfile.ZipFile(io.BytesIO(download.content))
        names = archive.namelist()
        markdown = [name for name in names if name.endswith(".md")]
        review_md = next(
            (name for name in markdown if "2026-11-09" in name), markdown[0]
        )
        rendered = archive.read(review_md).decode("utf-8")
        report["delivery"] = {
            "id": delivery["id"],
            "target": delivery["target"],
            "status": delivery["status"],
            "degraded": delivery.get("degraded"),
            "manifest_paths": [item["path"] for item in manifest["targets"]],
            "zip_entries": names,
            "review_markdown": review_md,
            "review_markdown_bytes": len(rendered.encode("utf-8")),
            "has_section_heading": "##" in rendered,
        }

        # ------------------------------------------------------------------
        # 链路 B：Embedding 蓝绿换维（真实模型 1024 → 768 → 回滚）
        # ------------------------------------------------------------------
        report["embedding_initial"] = embedding_state(client)
        base_stats = chunk_stats(workspace_id, "document_chunks")
        report["chunk_stats_before_switch"] = base_stats
        assert (
            base_stats["rows"] > 0 and base_stats["embedded"] == base_stats["rows"]
        ), base_stats
        assert base_stats["dims"] == 1024, base_stats

        # 先把当前 1024 维环境配置固化为 active profile，作为回滚基线。
        baseline = post(
            client,
            "/settings/embedding/profiles",
            {
                "provider": "openai",
                "model": "qwen3-embedding:0.6b",
                "dimension": 1024,
                "api_key": "local",
                "confirm": True,
            },
        )
        assert baseline.status_code == 202, baseline.text
        baseline_profile_id = baseline.json()["profile"]["id"]
        report["baseline_profile"] = {
            "id": baseline_profile_id,
            "chunk_table": baseline.json()["profile"]["chunk_table"],
            "job_id": baseline.json()["job_id"],
        }
        baseline_done = wait_reembed(client, baseline_profile_id)
        report["baseline_profile"]["reembed"] = baseline_done["reembed"]
        report["baseline_profile"]["coverage"] = baseline_done["profile"]["coverage"]
        assert baseline_done["reembed"]["status"] == "succeeded", baseline_done
        assert baseline_done["profile"]["coverage"] == 1.0, baseline_done

        report["baseline_activation"] = activate_profile(
            workspace_id, baseline_profile_id
        )
        active_state = embedding_state(client)
        assert str(active_state["active_profile"]["id"]) == str(baseline_profile_id), (
            active_state
        )

        switched = post(
            client,
            "/settings/embedding/profiles",
            {
                "provider": "openai",
                "model": "nomic-embed-text",
                "dimension": 768,
                "api_key": "local",
                "confirm": True,
            },
        )
        assert switched.status_code == 202, switched.text
        switched_profile_id = switched.json()["profile"]["id"]
        report["switch_profile"] = {
            "id": switched_profile_id,
            "model": switched.json()["profile"]["model"],
            "dimension": switched.json()["profile"]["dimension"],
            "chunk_table": switched.json()["profile"]["chunk_table"],
            "job_id": switched.json()["job_id"],
        }
        assert switched.json()["profile"]["chunk_table"] == "document_chunks_v768"

        switched_done = wait_reembed(client, switched_profile_id)
        report["switch_profile"]["reembed"] = switched_done["reembed"]
        report["switch_profile"]["coverage"] = switched_done["profile"]["coverage"]
        assert switched_done["reembed"]["status"] == "succeeded", switched_done
        assert switched_done["profile"]["coverage"] == 1.0, switched_done

        new_stats = chunk_stats(workspace_id, "document_chunks_v768")
        report["chunk_stats_v768"] = new_stats
        assert new_stats["embedded"] == base_stats["rows"], (new_stats, base_stats)
        assert new_stats["dims"] == 768, new_stats

        report["switch_activation"] = activate_profile(
            workspace_id, switched_profile_id
        )
        active_state = embedding_state(client)
        report["embedding_after_switch"] = active_state
        assert str(active_state["active_profile"]["id"]) == str(switched_profile_id)

        after_search, after_attempts = search_with_retry(
            client, "HNSW 语句超时与查询向量缓存"
        )
        report["search_after_switch"] = {
            "matched_by": [item["matched_by"] for item in after_search["items"]],
            "paths": [item["path"] for item in after_search["items"]],
            "degraded": after_search["degraded"],
            "degraded_reasons": after_search["degraded_reasons"],
            "attempts": after_attempts,
        }
        # 先落一次盘：即使后续断言失败也保留蓝绿切换的观测数据。
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert after_search["items"], (
            f"vector search after switch returned no items: {after_attempts}"
        )

        rollback = post(client, "/settings/embedding/rollback")
        assert rollback.status_code == 200, rollback.text
        rolled = embedding_state(client)
        report["rollback"] = {
            "active_profile": rolled["active_profile"],
            "profiles": [
                {
                    "id": item["id"],
                    "model": item["model"],
                    "dimension": item["dimension"],
                    "status": item["status"],
                    "retire_at": item.get("retire_at"),
                }
                for item in rolled["profiles"]
            ],
        }
        assert str(rolled["active_profile"]["id"]) == str(baseline_profile_id), rolled

        rollback_search, rollback_attempts = search_with_retry(
            client, "HNSW 语句超时与查询向量缓存"
        )
        report["search_after_rollback"] = {
            "matched_by": [item["matched_by"] for item in rollback_search["items"]],
            "paths": [item["path"] for item in rollback_search["items"]],
            "degraded": rollback_search["degraded"],
            "degraded_reasons": rollback_search["degraded_reasons"],
            "attempts": rollback_attempts,
        }
        assert rollback_search["items"], (
            f"vector search after rollback returned no items: {rollback_attempts}"
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: e2e_realmodel.py <invite-code>")
    raise SystemExit(main(sys.argv[1]))
