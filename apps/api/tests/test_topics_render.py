"""专题页重渲染（AC-40 / FX-07）。"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from mylife_api.services_review import wait_for_run
from mylife_api.settings import settings
from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

TOPIC_REL = "wiki/复盘/专题/错误清单.md"


def _note(day: str, *, root_cause: str, cost: str, method: str) -> str:
    return (
        "---\n"
        f"date: {day}\n"
        "---\n"
        f"# {day}\n\n"
        "## 做错的事\n"
        f"- 事件：缓存问题 | 根因：{root_cause} | 代价：{cost} | "
        "正确做法：加保护 | 复发触发：大促\n\n"
        "## 正确方法论\n"
        f"- 名称：{method} | 来源：实践 | 场景：缓存 | 步骤：估算容量;分层校验 | "
        "边界：删除场景 | 落地：本月接入 | 验证：压测通过\n"
    )


FILES = {
    "日记/2026/10.25.md": _note(
        "2026-10-25", root_cause="未加布隆过滤器", cost="2h", method="布隆过滤器"
    ),
    "日记/2026/10.26.md": _note(
        "2026-10-26", root_cause="未加布隆过滤器", cost="半天", method="布隆过滤器"
    ),
    "日记/2026/10.27.md": _note(
        "2026-10-27", root_cause="缓存雪崩", cost="3h", method="随机过期"
    ),
}


async def _generate(client, workspace_id: str, period_key: str):
    token = await helpers.csrf(client)
    response = await client.post(
        f"{API}/reviews",
        json={"kind": "daily", "period_key": period_key, "allow_empty": True},
        headers=helpers.csrf_headers(token),
    )
    assert response.status_code in {200, 201}, response.text
    body = response.json()
    state = await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(body["run_id"]))
    assert state == "succeeded", state
    return body


def _latest_bundle_file(workspace_id: str, relative: str) -> Path | None:
    base = Path(settings.data_root) / "workspaces" / workspace_id / "artifacts" / "deliveries"
    if not base.exists():
        return None
    candidates = sorted(
        (item / "files" / relative for item in base.iterdir() if item.is_dir()),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@pytest.mark.asyncio
async def test_topic_merge_dedupe_conflict_and_human_region(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="topic1@example.com", files=FILES
    )
    for day in ("2026-10-25", "2026-10-26", "2026-10-27"):
        await _generate(client, workspace_id, day)

    items = admin_conn.execute(
        "SELECT canonical_key, occurrence_count, status, conflicts_json "
        "FROM topic_items WHERE workspace_id = %s AND topic = 'errors' "
        "ORDER BY canonical_key",
        (workspace_id,),
    ).fetchall()
    assert len(items) == 2, "同一 canonical_key 只能有一条"
    by_key = {row[0]: row for row in items}
    repeated = max(by_key.values(), key=lambda row: row[1])
    assert repeated[1] == 2
    assert repeated[2] == "pending"
    conflicts = repeated[3] or []
    assert any(item["field"] == "cost" for item in conflicts), "冲突字段必须显式记录"

    # 决策 4B：首见条目需人工确认后才进入专题页。
    pending = await client.get(f"{API}/topics/错误清单/items?status=pending")
    assert pending.status_code == 200
    for item in pending.json()["items"]:
        csrf = await helpers.csrf(client)
        confirmed = await client.post(
            f"{API}/topics/错误清单/items/{item['item_id']}/confirm",
            json={"decision": "accept"},
            headers=helpers.csrf_headers(csrf),
        )
        assert confirmed.status_code == 200, confirmed.text
    await _generate(client, workspace_id, "2026-10-27")

    topic_file = _latest_bundle_file(workspace_id, TOPIC_REL)
    assert topic_file is not None
    text = topic_file.read_text(encoding="utf-8")
    assert "缓存" in text
    assert "冲突（未自动择一）" in text
    assert text.count("出现记录") == 2
    assert "2026-10-25" in text and "2026-10-26" in text

    # 人工区保留：在标记外追加文本后重渲染
    edited = text.rstrip("\n") + "\n\n## 人工段落\n保留我\n"
    topic_file.write_text(edited, encoding="utf-8")
    await _generate(client, workspace_id, "2026-10-25")
    merged = _latest_bundle_file(workspace_id, TOPIC_REL).read_text(encoding="utf-8")
    assert "## 人工段落\n保留我" in merged

    count = admin_conn.execute(
        "SELECT occurrence_count FROM topic_items WHERE workspace_id = %s "
        "AND canonical_key = %s",
        (workspace_id, repeated[0]),
    ).fetchone()[0]
    assert count == 2, "重跑不得重复累加"

    # 标记丢失 → 转 manual，不覆盖
    broken = merged.replace("<!-- mylife:auto:start -->", "").replace(
        "<!-- mylife:auto:end -->", ""
    )
    _latest_bundle_file(workspace_id, TOPIC_REL).write_text(broken, encoding="utf-8")
    body = await _generate(client, workspace_id, "2026-10-27")
    state = admin_conn.execute(
        "SELECT status FROM topic_files WHERE workspace_id = %s AND topic = 'errors'",
        (workspace_id,),
    ).fetchone()
    assert state is not None and state[0] == "manual"
    delivery = admin_conn.execute(
        "SELECT status, error_code FROM delivery_records WHERE workspace_id = %s "
        "AND review_id = %s ORDER BY version DESC LIMIT 1",
        (workspace_id, body["review_id"]),
    ).fetchone()
    assert delivery[0] == "conflict"
    assert delivery[1] == "GIT_MARKER_BROKEN"
    preserved = _latest_bundle_file(workspace_id, TOPIC_REL).read_text(encoding="utf-8")
    assert "<!-- mylife:auto:start -->" not in preserved
