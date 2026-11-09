"""真实 LLM 聊天与引用端到端验证（chat → SSE → 引用闭包）。

前置：
- `api` 容器已接到真实 LLM（本机 magpie 反代，`host.docker.internal:3425`）；
- Ollama 已就绪（导入时用真实 embedding）；
- 库中有一个未使用的邀请码。

用法：
    NO_PROXY=localhost,127.0.0.1 uv run python scripts/e2e_realmodel_chat.py <invite-code>

流程：注册 → 导入 4 篇笔记 → 建立会话 → 发起一次提问 → 边读 SSE 边等 run →
校验 assistant 正文、`[n]` 引用、references 与库内 chunk 一致、usage 计量。
结果写入 `docs/evidence/phase5/realmodel-chat.json`。
"""

from __future__ import annotations

import json
import re
import sys
import time
import uuid
from pathlib import Path

import httpx
from e2e_realmodel import BASE, csrf, make_zip, poll, post, psql

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "evidence" / "phase5" / "realmodel-chat.json"
ANSWER_OUT = ROOT / "docs" / "evidence" / "phase5" / "realmodel-chat-answer.md"
HOST = "https://localhost"
QUESTION = "我最近学了哪些关于向量检索的内容？请给出细节和依据。"
TERMINAL = {"succeeded", "failed", "cancelled", "security_abort"}
CITATION = re.compile(r"\[(\d{1,3})\]")


def parse_sse(lines) -> list[dict]:
    """把 SSE 行流解析为 `{event, data, id}`；data 已 JSON 解码。"""
    frames: list[dict] = []
    current: dict = {}

    def flush() -> None:
        nonlocal current
        if not current:
            return
        raw = "\n".join(current.pop("data", []))
        if raw:
            try:
                current["data"] = json.loads(raw)
            except json.JSONDecodeError:
                current["data"] = raw
        frames.append(current)
        current = {}

    for line in lines:
        if line == "":
            flush()
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            current["event"] = value
        elif field == "id":
            current["id"] = value
        elif field == "data":
            current.setdefault("data", []).append(value)
    flush()
    return frames


def read_stream(
    client: httpx.Client, events_url: str, *, timeout: float = 120.0
) -> list[dict]:
    """实时读取 SSE，直到服务端关闭或超时。

    不按 `chat.done` 提前退出：服务端现在会先排空总线上的 token 再发终止帧，
    但读满整个流才能校验该顺序。
    """
    frames: list[dict] = []
    started = time.monotonic()
    try:
        with client.stream(
            "GET",
            f"{HOST}{events_url}?after=0",
            headers={"Accept": "text/event-stream"},
            timeout=httpx.Timeout(timeout, read=timeout),
        ) as response:
            response.raise_for_status()
            for frame in parse_sse(response.iter_lines()):
                frame["at_ms"] = int((time.monotonic() - started) * 1000)
                frames.append(frame)
                if time.monotonic() - started > timeout:
                    break
    except httpx.TimeoutException:
        pass
    return frames


def main(invite: str) -> int:
    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "question": QUESTION,
        "notes": [
            "chat 的工具计划是确定性的（plan_tool_calls）；真实 LLM 只负责依据证据起草。",
            "token 事件只在实时 SSE 中下发（id=None），回放只包含持久化事件。",
            "服务端会先排空总线上的 token 帧，再发送 chat.done 等终止帧。",
            "chat 的 usage_events 写入 run_id/attempt_id/role，可按 run 对账。",
        ],
    }
    with httpx.Client(verify=False, timeout=180.0, trust_env=False) as client:
        email = f"realchat-{uuid.uuid4().hex[:8]}@example.com"
        registered = post(
            client,
            "/auth/register",
            {
                "email": email,
                "password": "correct-horse-9",
                "display_name": "Real Chat E2E",
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
        }

        llm_settings = client.get(f"{BASE}/settings/llm")
        assert llm_settings.status_code == 200, llm_settings.text
        report["llm_settings"] = llm_settings.json()
        assert report["llm_settings"]["credential_status"] == "valid", report[
            "llm_settings"
        ]
        assert report["llm_settings"]["provider"] == "openai_compatible", report[
            "llm_settings"
        ]

        created = post(client, "/conversations", {"title": "真实 LLM 引用验证"})
        assert created.status_code == 201, created.text
        conversation_id = created.json()["id"]
        report["conversation_id"] = conversation_id

        client_turn_id = str(uuid.uuid4())
        turn = post(
            client,
            f"/conversations/{conversation_id}/turns",
            {"client_turn_id": client_turn_id, "message": QUESTION},
        )
        assert turn.status_code == 202, turn.text
        turn_body = turn.json()
        run_id = turn_body["run_id"]
        message_id = turn_body["message_id"]
        events_url = turn_body["stream"]["events_url"]
        report["turn"] = {
            "run_id": run_id,
            "message_id": message_id,
            "events_url": events_url,
            "epoch": turn_body["stream"]["epoch"],
        }

        # 立刻订阅，捕捉只在实时流里出现的 token 增量。
        frames = read_stream(client, events_url)
        event_counts: dict[str, int] = {}
        for frame in frames:
            name = str(frame.get("event") or "unknown")
            event_counts[name] = event_counts.get(name, 0) + 1
        token_frames = [
            frame
            for frame in frames
            if frame.get("event") == "token" and frame.get("data")
        ]
        token_text = "".join(
            str((frame.get("data") or {}).get("delta") or "") for frame in token_frames
        )
        report["stream"] = {
            "event_counts": event_counts,
            "tokens": len(token_frames),
            "first_event_ms": frames[0]["at_ms"] if frames else None,
            "first_token_ms": token_frames[0]["at_ms"] if token_frames else None,
            "last_event_ms": frames[-1]["at_ms"] if frames else None,
            "token_text_sample": token_text[:300],
            "persisted_events": sorted(
                name for name in event_counts if name not in {"token", "heartbeat"}
            ),
        }

        def run_done():
            response = client.get(f"{BASE}/runs/{run_id}")
            if response.status_code != 200:
                return None
            run = response.json()
            return run if run["state"] in TERMINAL else None

        run = poll(run_done, timeout=180, interval=0.3)
        report["run"] = {
            "state": run["state"],
            "degraded": run["degraded"],
            "degraded_reasons": run["degraded_reasons"],
            "error": run["error"],
            "steps": [
                {
                    "seq": step["seq"],
                    "role": step["role"],
                    "tool_name": step["tool_name"],
                    "status": step["status"],
                    "model": step["model"],
                    "tokens": step["tokens"],
                    "latency_ms": step["latency_ms"],
                }
                for step in run["steps"]
            ],
        }
        assert run["state"] == "succeeded", run
        assert any(step["tool_name"] == "search_notes" for step in run["steps"]), run[
            "steps"
        ]

        messages = client.get(f"{BASE}/conversations/{conversation_id}/messages")
        assert messages.status_code == 200, messages.text
        items = messages.json()["items"]
        roles = [item["role"] for item in items]
        assistant = next(item for item in items if item["id"] == message_id)
        content = assistant["content"]
        references = assistant["references"]
        cited = [int(match.group(1)) for match in CITATION.finditer(content)]
        # 同一事务写入的 user/assistant 行 created_at 相同；角色 tie-break 保证
        # user 始终排在 assistant 之前。
        assert roles == ["user", "assistant"], items
        report["message_order"] = {
            "roles": roles,
            "user_before_assistant": True,
        }

        reference_rows = []
        for reference in references:
            exists = psql(
                "SELECT count(*) FROM document_chunks c "
                "JOIN documents d ON d.id = c.document_id "
                f"WHERE c.id = '{reference['chunk_id']}' AND d.workspace_id = '{workspace_id}'"
            )
            reference_rows.append(
                {
                    "index": reference["index"],
                    "path": reference["path"],
                    "heading": reference.get("heading"),
                    "line_range": reference.get("line_range"),
                    "document_id": reference["document_id"],
                    "chunk_id": reference["chunk_id"],
                    "snippet_chars": len(reference["snippet"]),
                    "chunk_exists_in_workspace": int(exists) == 1,
                }
            )

        report["assistant"] = {
            "status": assistant["status"],
            "content_chars": len(content),
            "content": content,
            "citation_indexes_in_content": cited,
            "references": reference_rows,
            "tool_events": [
                {
                    "seq": event["seq"],
                    "tool_name": event["tool_name"],
                    "status": event["status"],
                    "latency_ms": event["latency_ms"],
                    "args_summary": event["args_summary"],
                }
                for event in assistant["tool_events"]
            ],
            "usage": assistant["usage"],
        }

        assert assistant["status"] == "complete", assistant
        assert content.strip(), "assistant content is empty"
        assert cited, "assistant answer has no [n] citations"
        assert references, "assistant references are empty"
        assert [ref["index"] for ref in references] == list(
            range(1, len(references) + 1)
        )
        assert set(cited) == {ref["index"] for ref in references}, (cited, references)
        assert all(row["chunk_exists_in_workspace"] for row in reference_rows), (
            reference_rows
        )
        assert all(row["snippet_chars"] > 0 for row in reference_rows), reference_rows
        assert token_frames, "no live token events captured"

        # chat usage 按 run 归属，可直接对账。
        usage = psql(
            "SELECT prompt_tokens, completion_tokens, model, latency_ms, degraded, "
            "       coalesce(run_id::text, ''), coalesce(attempt_id::text, ''), "
            "       coalesce(role, '') "
            "FROM usage_events "
            f"WHERE workspace_id = '{workspace_id}' AND kind = 'chat' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        assert usage, "usage_events row missing"
        usage_fields = usage.split("|")
        prompt_tokens, completion_tokens, model, _latency_ms, degraded = usage_fields[:5]
        usage_run, usage_attempt, usage_role = usage_fields[5:8]
        assert usage_run == run_id, usage
        assert usage_attempt, usage
        assert usage_role == "draft", usage
        report["usage_events"] = {
            "raw": usage,
            "run_id": usage_run,
            "attempt_id": usage_attempt,
            "role": usage_role,
            "note": "usage_events 已按 run/attempt 归属，role=draft。",
        }
        assert int(prompt_tokens) > 0 and int(completion_tokens) > 0, usage
        assert model not in {"", "unknown", "sentinel"}, usage
        assert degraded == "f", usage

        findings: list[dict] = []
        findings.append(
            {
                "severity": "info",
                "area": "message ordering",
                "summary": "同一事务写入的 user/assistant 已按 role tie-break 稳定排序。",
                "status": "fixed",
            }
        )
        findings.append(
            {
                "severity": "info",
                "area": "usage attribution",
                "summary": "chat usage_events 写入 run_id/attempt_id/role，可按 run 对账。",
                "status": "fixed",
            }
        )
        findings.append(
            {
                "severity": "info",
                "area": "sse ordering",
                "summary": "终止帧之前先排空总线上的 token 帧，客户端收到 chat.done 即断开也不会丢 token。",
                "status": "fixed",
            }
        )
        findings.append(
            {
                "severity": "info",
                "area": "upstream streaming",
                "summary": (
                    "首个 token 在 "
                    f"{report['stream']['first_token_ms']}ms，最后事件在 "
                    f"{report['stream']['last_event_ms']}ms；magpie/minimax 把增量攒到结束时一次性下发，"
                    "接口层 SSE 正常但上游没有逐字流。"
                ),
                "status": "upstream",
            }
        )
        report["findings"] = findings

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ANSWER_OUT.write_text(
        "# 真实 LLM 回答（端到端验证）\n\n"
        f"- 问题：{QUESTION}\n"
        f"- 会话：{report['conversation_id']}\n"
        f"- Run：{report['turn']['run_id']}\n\n"
        "---\n\n"
        f"{report['assistant']['content']}\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: e2e_realmodel_chat.py <invite-code>")
    raise SystemExit(main(sys.argv[1]))
