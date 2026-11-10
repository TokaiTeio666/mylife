"""每日复盘 Writer 的语义抽取集成测试（不调用真实模型）。"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from mylife_agent.llm import LLMUnavailable, LLMUsage

from mylife_api.services_review import wait_for_run
from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

DOCS = {
    "日记/2026/10.27.md": (
        "---\n"
        "date: 2026-10-27\n"
        "---\n"
        "# 10-27\n\n"
        "今天发布前忘了备份，回滚花了一小时。\n"
        "下次先把备份写进检查清单。\n"
    ),
}


class FakeSemanticProvider:
    model = "fake-semantic"
    usage = None

    def __init__(self) -> None:
        self.prompt = ""

    async def stream(self, request):
        self.prompt = request.question
        evidence_ids = re.findall(r"id=(e\d+)", request.question)
        evidence_id = evidence_ids[0] if evidence_ids else "e1"
        payload = {
            "mistakes": [
                {
                    "event": "发布前忘记备份",
                    "root_cause": "检查清单里没有备份项",
                    "cost": "回滚一小时",
                    "correct_action": "把备份写进发布检查清单",
                    "recurrence_trigger": "每次发布前",
                    "evidence_ids": [evidence_id],
                }
            ],
            "methods": [
                {
                    "name": "发布前先备份",
                    "source": "本次复盘的教训",
                    "applicable_scenario": "任何会改数据的发布",
                    "steps": ["确认备份完成", "再执行发布", "失败时用备份回滚"],
                    "boundaries": "只读发布不适用",
                    "landing_action": "检查清单新增备份项",
                    "verification_criteria": "发布记录中能看到备份时间",
                    "evidence_ids": [evidence_id],
                }
            ],
        }
        text = json.dumps(payload, ensure_ascii=False)
        for start in range(0, len(text), 40):
            yield text[start : start + 40]
        self.usage = LLMUsage(
            prompt_tokens=321,
            completion_tokens=88,
            model=self.model,
            latency_ms=17,
        )


class FailingSemanticProvider:
    model = "failing-semantic"
    usage = None

    async def stream(self, request):
        if False:  # pragma: no cover - 让函数保持 async generator
            yield ""
        raise LLMUnavailable("upstream down")


async def _generate(client, workspace_id: str) -> tuple[dict, str]:
    token = await helpers.csrf(client)
    response = await client.post(
        f"{API}/reviews",
        json={"kind": "daily", "period_key": "2026-10-27"},
        headers=helpers.csrf_headers(token),
    )
    assert response.status_code in {200, 201}, response.text
    body = response.json()
    state = await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(body["run_id"]))
    return body, state


@pytest.mark.asyncio
async def test_daily_review_uses_semantic_writer(client, admin_conn, monkeypatch):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="semantic-writer@example.com", files=DOCS
    )
    fake = FakeSemanticProvider()

    async def _fake_builder(session, *, workspace_id):
        return fake

    import mylife_api.services_llm as services_llm

    monkeypatch.setattr(services_llm, "build_llm_for_workspace", _fake_builder)

    body, state = await _generate(client, workspace_id)
    assert state == "succeeded", state
    assert "今天发布前忘了备份" in fake.prompt

    entries = admin_conn.execute(
        "SELECT item_type, canonical_key, fields_json, source_path, line_start, line_end "
        "FROM review_entries WHERE workspace_id = %s AND review_id = %s AND version = %s "
        "ORDER BY item_type",
        (workspace_id, body["review_id"], body["version"]),
    ).fetchall()
    assert {row[0] for row in entries} == {"error", "method"}
    for _item_type, _key, fields, path, line_start, line_end in entries:
        assert path == "日记/2026/10.27.md"
        assert line_start is not None and line_end is not None
        assert json.loads(fields) if isinstance(fields, str) else fields

    model = admin_conn.execute(
        "SELECT model FROM reviews WHERE workspace_id = %s AND id = %s",
        (workspace_id, body["review_id"]),
    ).fetchone()[0]
    assert model == "llm-writer:fake-semantic"

    citations = admin_conn.execute(
        "SELECT count(*) FROM review_citations WHERE workspace_id = %s "
        "AND review_id = %s AND version = %s",
        (workspace_id, body["review_id"], body["version"]),
    ).fetchone()[0]
    assert citations >= 2

    usage = admin_conn.execute(
        "SELECT kind, role, prompt_tokens, completion_tokens FROM usage_events "
        "WHERE workspace_id = %s AND run_id = %s",
        (workspace_id, body["run_id"]),
    ).fetchall()
    assert usage and usage[0][0] == "review" and usage[0][1] == "writer"
    assert usage[0][2] == 321 and usage[0][3] == 88


@pytest.mark.asyncio
async def test_daily_review_falls_back_when_model_unavailable(
    client, admin_conn, monkeypatch
):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="semantic-fallback@example.com", files=DOCS
    )

    async def _failing_builder(session, *, workspace_id):
        return FailingSemanticProvider()

    import mylife_api.services_llm as services_llm

    monkeypatch.setattr(services_llm, "build_llm_for_workspace", _failing_builder)

    body, state = await _generate(client, workspace_id)
    assert state == "succeeded", state

    detail = await client.get(f"{API}/reviews/{body['review_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["degraded"] is True
    # 结构化笔记里没有可被确定性解析器识别的条目 → 哨兵章节
    assert "本日无明显错误记录" in detail.json()["content_md"]
