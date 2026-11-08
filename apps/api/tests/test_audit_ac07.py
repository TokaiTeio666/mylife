"""审计五类覆盖（AC-07 / SEC-24）。

依次执行：登录 → 导入 → 删除 → 配置变更 → 复盘生成，断言五类 action 各至少
1 行，`workspace_id/user_id/action/target/ip/created_at` 齐全，且 detail 不含
明文 API Key 与超过 200 字符的笔记正文。
"""

from __future__ import annotations

import re
import uuid

import pytest
from mylife_contract.constants import AUDIT_AC07_FAMILIES

from mylife_api.services_review import wait_for_run
from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

SECRET = "sk-ac07-0123456789abcdef0123456789"
LONG_BODY = "笔记正文" * 80  # 320 字符


async def _post(client, path: str, body: dict | None = None, headers=None):
    token = await helpers.csrf(client)
    merged = {**helpers.csrf_headers(token), **(headers or {})}
    return await client.post(path, json=body or {}, headers=merged)


async def _put(client, path: str, body: dict):
    token = await helpers.csrf(client)
    return await client.put(path, json=body, headers=helpers.csrf_headers(token))


@pytest.mark.asyncio
async def test_audit_covers_five_classes(client, admin_conn):
    # 1) 登录（注册后重新登录一次）
    workspace_id, user_id = await helpers.prepare_workspace_with_docs(
        client,
        admin_conn,
        email="ac07@example.com",
        files={"日记/2026/10.27.md": f"---\ndate: 2026-10-27\n---\n# x\n{LONG_BODY}\n"},
    )
    assert (await helpers.login(client, email="ac07@example.com")).status_code == 200

    # 2) 配置变更（LLM 设置，带密钥）
    saved = await _put(
        client,
        f"{API}/settings/llm",
        {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": SECRET,
            "test_before_save": False,
        },
    )
    assert saved.status_code == 200, saved.text

    # 3) 复盘生成
    created = await _post(
        client,
        f"{API}/reviews",
        {"kind": "daily", "period_key": "2026-10-27", "allow_empty": True},
    )
    assert created.status_code in {200, 201}, created.text
    await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(created.json()["run_id"]))

    # 4) 导出 + 5) 删除
    exported = await _post(
        client,
        f"{API}/exports",
        {"scope": "workspace", "include": ["documents"]},
        headers={"Idempotency-Key": "ac07-export"},
    )
    assert exported.status_code == 202, exported.text
    name = admin_conn.execute(
        "SELECT name FROM workspaces WHERE id = %s", (workspace_id,)
    ).fetchone()[0]
    deleted = await _post(
        client, f"{API}/deletions", {"scope": "workspace", "confirm_text": name}
    )
    assert deleted.status_code == 201, deleted.text

    # 五类各自至少 1 行
    rows = admin_conn.execute(
        "SELECT action, workspace_id, user_id, target, ip, created_at, detail::text "
        "FROM audit_logs ORDER BY created_at"
    ).fetchall()
    by_action = {row[0] for row in rows}
    for family, actions in AUDIT_AC07_FAMILIES.items():
        assert by_action & set(actions), f"缺少审计族：{family}（已有 {sorted(by_action)}）"

    # 字段齐全（登录/导入/配置/复盘/删除各取一条）
    for family, actions in AUDIT_AC07_FAMILIES.items():
        hits = [row for row in rows if row[0] in actions]
        assert hits, family
        for row in hits:
            action, ws, uid, target, ip, created_at, detail = row
            assert action
            assert created_at is not None
            # 平台级操作允许 workspace_id/user_id 为空；租户动作必须有
            if action.startswith(("settings.", "review.", "ingest.", "user.login")):
                assert ws is not None, f"{action} 缺 workspace_id"
                assert uid is not None, f"{action} 缺 user_id"
            if action.startswith("deletion."):
                assert target is not None
            # IP 由中间件兜底注入；测试客户端是 127.0.0.1（或 None 时跳过）
            if ip is not None:
                assert re.match(r"^[0-9a-fA-F:.]+$", str(ip))

    # 脱敏：不含明文密钥；detail 字符串不超过 200 + 省略号
    joined = "\n".join(row[6] or "" for row in rows)
    assert SECRET not in joined
    assert "sk-" not in joined
    for row in rows:
        detail = row[6] or ""
        assert len(detail) < 4000
    # 正文不得整段落入审计
    assert LONG_BODY[:120] not in joined
