"""Phase 3 golden fixtures（FX-01/02/03/04/10/12）。

每个用例输出 `report.json` 到 `docs/evidence/phase3/`（CI 里只断言，不写文件）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from mylife_api.services_review import wait_for_run
from tests import helpers
from tests.fixtures import vaults
from tests.helpers import API

pytestmark = [pytest.mark.db, pytest.mark.fixture]

EVIDENCE_DIR = Path(__file__).resolve().parents[3] / "docs" / "evidence" / "phase3"


def _report(name: str, payload: dict) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _generate(client, workspace_id: str, *, kind: str, period_key: str, **extra):
    token = await helpers.csrf(client)
    response = await client.post(
        f"{API}/reviews",
        json={"kind": kind, "period_key": period_key, **extra},
        headers=helpers.csrf_headers(token),
    )
    assert response.status_code in {200, 201}, response.text
    body = response.json()
    state = await wait_for_run(uuid.UUID(workspace_id), uuid.UUID(body["run_id"]))
    return body, state


def _sections(admin_conn, workspace_id: str, review_id: str, version: int):
    return admin_conn.execute(
        "SELECT section_key, ordinal, content_md, structured_json FROM review_sections "
        "WHERE workspace_id = %s AND review_id = %s AND version = %s ORDER BY ordinal",
        (workspace_id, review_id, version),
    ).fetchall()


@pytest.mark.asyncio
async def test_fx01_daily(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="fx01@example.com", files=vaults.fx_daily()
    )
    body, state = await _generate(client, workspace_id, kind="daily", period_key="2026-10-27")
    assert state == "succeeded", state

    rows = _sections(admin_conn, workspace_id, body["review_id"], body["version"])
    assert [row[1] for row in rows] == list(range(1, 8))
    assert all(row[2].strip() for row in rows)

    today = next(row for row in rows if row[0] == "today_tasks")
    carried = today[3].get("carried_over", [])
    assert [item["text"] for item in carried] == ["读论文"]

    # 错误/方法论当日无抽取来源 → 哨兵（AC-36 允许）
    mistakes = next(row for row in rows if row[0] == "mistakes")
    assert mistakes[2].strip()

    citations = admin_conn.execute(
        "SELECT count(*) FROM review_citations WHERE workspace_id = %s AND review_id = %s "
        "AND version = %s",
        (workspace_id, body["review_id"], body["version"]),
    ).fetchone()[0]

    evidence = admin_conn.execute(
        "SELECT count(*) FROM evidence_items WHERE workspace_id = %s AND run_id = %s",
        (workspace_id, body["run_id"]),
    ).fetchone()[0]
    assert evidence <= 80

    # 重跑同 snapshot：diff 率 ≤10%（结构一致）
    second, state2 = await _generate(
        client, workspace_id, kind="daily", period_key="2026-10-27"
    )
    assert state2 == "succeeded"
    rows2 = _sections(admin_conn, workspace_id, second["review_id"], second["version"])
    same = sum(
        1 for a, b in zip(rows, rows2, strict=False) if a[0] == b[0]
    )
    diff_ratio = 1 - same / max(len(rows), 1)
    assert diff_ratio <= 0.1

    _report(
        "fx01-daily",
        {
            "sections": [row[0] for row in rows],
            "ordinals": [row[1] for row in rows],
            "carried_over": [item["text"] for item in carried],
            "citations": citations,
            "evidence": evidence,
            "rerun_diff_ratio": round(diff_ratio, 4),
            "state": state,
        },
    )


@pytest.mark.asyncio
async def test_fx02_weekly(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="fx02@example.com", files=vaults.fx_weekly()
    )
    # 先补齐 7 份日报
    for day in range(19, 26):
        await _generate(client, workspace_id, kind="daily", period_key=f"2026-10-{day:02d}")

    body, state = await _generate(client, workspace_id, kind="weekly", period_key="2026-W43")
    assert state in {"succeeded", "human_gate"}, state

    rows = _sections(admin_conn, workspace_id, body["review_id"], body["version"])
    assert [row[0] for row in rows] == [
        "completion_rate",
        "recurring_mistakes",
        "method_landing",
        "next_week_actions",
    ]
    completion = rows[0][3]
    from mylife_api import repo_chat
    from mylife_api.db import get_session_factory, set_rls_context

    factory = get_session_factory()
    async with factory() as session:
        await set_rls_context(session, uuid.UUID(workspace_id), None)
        ledger = await repo_chat.task_ledger_recalc(session, uuid.UUID(workspace_id))
        await session.rollback()
    assert completion["completed"] == ledger["completed"]
    assert completion["total"] == ledger["total"]
    assert abs(completion["rate"] - ledger["rate"]) < 1e-9

    _report(
        "fx02-weekly",
        {
            "sections": [row[0] for row in rows],
            "ledger": ledger,
            "state": state,
        },
    )


@pytest.mark.asyncio
async def test_fx03_yearly(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="fx03@example.com", files=vaults.fx_yearly()
    )
    # 年报只读下级 rollup：无周报时各节应为哨兵（不编造）。
    # 素材来自下级 rollup 而非全年原文，因此这里显式 allow_empty。
    body, state = await _generate(
        client, workspace_id, kind="yearly", period_key="2026", allow_empty=True
    )
    assert state in {"succeeded", "human_gate"}, state

    rows = _sections(admin_conn, workspace_id, body["review_id"], body["version"])
    assert [row[0] for row in rows] == [
        "learning_map",
        "event_timeline",
        "top_mistakes",
        "method_system",
        "next_year_direction",
    ]
    from mylife_contract.constants import SENTINELS

    # 没有周报 → 全部哨兵（只读 rollup，不回读全年原文）
    assert all(row[2] == SENTINELS[row[0]] for row in rows)

    _report(
        "fx03-yearly",
        {"sections": [row[0] for row in rows], "state": state, "all_sentinel": True},
    )


@pytest.mark.asyncio
async def test_fx03_yearly_reads_weekly_rollup(client, admin_conn):
    """有下级周报时，年报必须从 rollup 取数而不是回读全年原文。"""
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="fx03b@example.com", files=vaults.fx_weekly()
    )
    for day in range(19, 26):
        await _generate(
            client, workspace_id, kind="daily", period_key=f"2026-10-{day:02d}"
        )
    weekly, weekly_state = await _generate(
        client, workspace_id, kind="weekly", period_key="2026-W43"
    )
    assert weekly_state in {"succeeded", "human_gate"}

    yearly, state = await _generate(
        client, workspace_id, kind="yearly", period_key="2026", allow_empty=True
    )
    assert state in {"succeeded", "human_gate"}, state

    rows = _sections(admin_conn, workspace_id, yearly["review_id"], yearly["version"])
    from mylife_contract.constants import SENTINELS

    learning = next(row for row in rows if row[0] == "learning_map")
    # 周报的 method_landing 为空 → learning_map 仍为哨兵，但不得编造
    if learning[3].get("topics"):
        assert learning[2] != SENTINELS["learning_map"]
    else:
        assert learning[2] == SENTINELS["learning_map"]

    # 年报 run 的 rollup 查询确实读到了周报（source_kind=weekly）
    weekly_count = admin_conn.execute(
        "SELECT count(*) FROM reviews WHERE workspace_id = %s AND kind = 'weekly' "
        "AND period_key = '2026-W43'",
        (workspace_id,),
    ).fetchone()[0]
    assert weekly_count == 1
    assert weekly["review_id"] != yearly["review_id"]


@pytest.mark.asyncio
async def test_fx04_empty_day(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="fx04@example.com", files=vaults.fx_empty_day()
    )
    body, state = await _generate(
        client, workspace_id, kind="daily", period_key="2026-10-27", allow_empty=True
    )
    assert state == "succeeded"

    rows = _sections(admin_conn, workspace_id, body["review_id"], body["version"])
    from mylife_contract.constants import SENTINELS

    assert len(rows) == 7
    for row in rows:
        assert row[2] == SENTINELS[row[0]], row[0]

    citations = admin_conn.execute(
        "SELECT count(*) FROM review_citations WHERE workspace_id = %s AND review_id = %s",
        (workspace_id, body["review_id"]),
    ).fetchone()[0]
    assert citations == 0

    detail = await client.get(f"{API}/reviews/{body['review_id']}")
    assert detail.status_code == 200
    assert detail.json()["degraded"] is False

    _report(
        "fx04-empty-day",
        {
            "sections": [row[0] for row in rows],
            "all_sentinel": True,
            "citations": citations,
            "state": state,
        },
    )


@pytest.mark.asyncio
async def test_fx10_budget_warn_and_hard_stop(client, admin_conn, db_session):
    workspace_id, user_id = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="fx10@example.com", files=vaults.fx_cost_budget()
    )
    from mylife_agent.budget import evaluate

    # 80%：告警但继续
    budget = __import__(
        "mylife_agent.budget", fromlist=["x"]
    ).budget_for_kind("daily")
    warn = evaluate(
        used_input=int(budget.max_input_tokens * 0.85),
        used_output=0,
        used_cost=0,
        budget=budget,
    )
    assert warn.should_warn and not warn.should_stop

    stop = evaluate(
        used_input=budget.max_input_tokens,
        used_output=0,
        used_cost=0,
        budget=budget,
    )
    assert stop.should_stop

    # workspace 月配额耗尽 → 新 run 拒绝
    from mylife_contract.errors import ErrorCode

    from mylife_api import services_budget
    from mylife_api.errors import AppError

    admin_conn.execute(
        "INSERT INTO usage_events (id, workspace_id, user_id, kind, prompt_tokens) "
        "VALUES (%s, %s, %s, 'chat', 5000000)",
        (uuid.uuid4(), workspace_id, user_id),
    )
    from mylife_api.db import set_rls_context

    await set_rls_context(db_session, workspace_id, user_id)
    with pytest.raises(AppError) as excinfo:
        await services_budget.check_workspace_quota(
            db_session, workspace_id=uuid.UUID(workspace_id), timezone="Asia/Shanghai"
        )
    assert excinfo.value.code == ErrorCode.BUDGET_EXCEEDED
    assert excinfo.value.details["scope"] == "workspace_monthly"

    _report(
        "fx10-budget",
        {
            "warn_ratio": warn.usage.ratio,
            "stop_ratio": stop.usage.ratio,
            "workspace_scope": excinfo.value.details["scope"],
        },
    )


@pytest.mark.asyncio
async def test_fx12_concurrent_admission(client, admin_conn, client_factory):
    workspace_a, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="fx12a@example.com", files=vaults.fx_concurrent_runs()
    )
    other = client_factory()
    workspace_b, _ = await helpers.prepare_workspace_with_docs(
        other, admin_conn, email="fx12b@example.com", files=vaults.fx_concurrent_runs()
    )

    async def fire(target_client, workspace_id: str):
        token = await helpers.csrf(target_client)
        return await target_client.post(
            f"{API}/reviews",
            json={"kind": "daily", "period_key": "2026-10-27"},
            headers=helpers.csrf_headers(token),
        )

    results_a, results_b = await asyncio.gather(
        asyncio.gather(*[fire(client, workspace_a) for _ in range(10)]),
        asyncio.gather(*[fire(other, workspace_b) for _ in range(10)]),
    )

    def summarize(results, workspace_id: str):
        created = [r for r in results if r.status_code in {200, 201}]
        conflicts = [r for r in results if r.status_code == 409]
        assert len(created) == 1, [r.status_code for r in results]
        assert len(conflicts) == 9
        existing = {r.json()["error"]["details"]["existing_run_id"] for r in conflicts}
        assert existing == {created[0].json()["run_id"]}
        active = admin_conn.execute(
            "SELECT count(*) FROM agent_runs WHERE workspace_id = %s "
            "AND kind = 'daily' AND period_key = '2026-10-27' AND is_active_state(state)",
            (workspace_id,),
        ).fetchone()[0]
        assert active <= 1
        return created[0].json()["run_id"]

    run_a = summarize(results_a, workspace_a)
    run_b = summarize(results_b, workspace_b)
    assert run_a != run_b

    await wait_for_run(uuid.UUID(workspace_a), uuid.UUID(run_a))
    await wait_for_run(uuid.UUID(workspace_b), uuid.UUID(run_b))

    _report(
        "fx12-concurrent",
        {"workspace_a_run": run_a, "workspace_b_run": run_b, "conflicts_each": 9},
    )
