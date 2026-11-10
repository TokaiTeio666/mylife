"""仪表盘与搜索三模式（AC-45…AC-48）。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from mylife_api.ids import uuid7
from mylife_api.services_dashboard import clear_dashboard_cache
from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

FILES = {
    "日记/2026/10.27.md": "---\ndate: 2026-10-27\n---\n# 10-27\n缓存与索引复盘。\n",
    "notes/cache.md": "---\ndate: 2026-10-27\n---\n# 缓存\n缓存穿透与布隆过滤器。\n",
}


def _seed_review(
    conn,
    *,
    workspace_id: str,
    kind: str,
    period_key: str,
    title: str,
    version: int = 1,
    status: str = "delivered",
    content: str = "缓存复盘正文",
) -> str:
    review_id = uuid7()
    conn.execute(
        "INSERT INTO reviews (id, workspace_id, kind, period_key, title, content, "
        " version, status, content_hash, git_path) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            review_id,
            workspace_id,
            kind,
            period_key,
            title,
            content,
            version,
            status,
            "h" * 64,
            f"wiki/复盘/{kind}/{period_key}.md",
        ),
    )
    conn.execute(
        "INSERT INTO review_search (id, workspace_id, review_id, version, kind, period_key, "
        " title, path, search_text, content_hash, is_current, occurred_on) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s)",
        (
            uuid.uuid4(),
            workspace_id,
            review_id,
            version,
            kind,
            period_key,
            title,
            f"wiki/复盘/{kind}/{period_key}.md",
            f"{title}\n{content}",
            "h" * 64,
            date.fromisoformat(period_key) if kind == "daily" else None,
        ),
    )
    return str(review_id)


def _seed_tasks(conn, *, workspace_id: str, days_ago: list[int], done: int = 0) -> None:
    for index, delta in enumerate(days_ago):
        conn.execute(
            "INSERT INTO tasks (id, workspace_id, key, text, status, first_seen, last_seen) "
            "VALUES (%s, %s, %s, %s, 'open', %s, %s)",
            (
                uuid7(),
                workspace_id,
                f"task-{index}",
                f"任务 {index}",
                date.today() - timedelta(days=delta),
                date.today() - timedelta(days=delta),
            ),
        )
    for index in range(done):
        conn.execute(
            "INSERT INTO tasks (id, workspace_id, key, text, status, first_seen, last_seen, "
            " done_date) VALUES (%s, %s, %s, %s, 'done', %s, %s, %s)",
            (
                uuid7(),
                workspace_id,
                f"done-{index}",
                f"完成任务 {index}",
                date.today() - timedelta(days=2),
                date.today(),
                date.today(),
            ),
        )


@pytest.mark.asyncio
async def test_latest_and_completion_rate_matches_sql(client, admin_conn):
    clear_dashboard_cache()
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dash1@example.com", files=FILES
    )
    _seed_review(
        admin_conn, workspace_id=workspace_id, kind="daily", period_key="2026-10-27",
        title="2026-10-27 每日复盘",
    )
    _seed_review(
        admin_conn, workspace_id=workspace_id, kind="monthly", period_key="2026-10",
        title="2026-10 每月复盘",
    )
    _seed_review(
        admin_conn, workspace_id=workspace_id, kind="yearly", period_key="2026",
        title="2026 年度复盘",
    )
    _seed_tasks(admin_conn, workspace_id=workspace_id, days_ago=[3, 5, 10], done=6)

    response = await client.get(f"{API}/dashboard/latest")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["daily"]["period_key"] == "2026-10-27"
    assert body["monthly"]["period_key"] == "2026-10"
    assert body["yearly"]["period_key"] == "2026"
    assert abs(body["completion_rate"] - 6 / 9) < 0.001
    assert body["open_tasks"] == 3

    sql = admin_conn.execute(
        "SELECT count(*) AS total, count(*) FILTER (WHERE status = 'done') AS completed "
        "FROM tasks WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()
    assert body["open_tasks"] == sql[0] - sql[1]
    assert abs(body["completion_rate"] - sql[1] / sql[0]) < 0.001


@pytest.mark.asyncio
async def test_days_open_by_workspace_timezone(client, admin_conn):
    clear_dashboard_cache()
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dash2@example.com", files={}
    )
    today = date.today()
    for index, delta in enumerate((3, 5, 10)):
        admin_conn.execute(
            "INSERT INTO tasks (id, workspace_id, key, text, status, first_seen, last_seen) "
            "VALUES (%s, %s, %s, %s, 'open', %s, %s)",
            (uuid7(), workspace_id, f"open-{index}", f"任务 {index}",
             today - timedelta(days=delta), today),
        )
    response = await client.get(f"{API}/dashboard/tasks")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["days_open"] for item in items] == [10, 5, 3]
    assert all(item["status"] == "carried" for item in items)
    assert response.json()["counts"]["carried"] == 3

    done = await client.get(f"{API}/dashboard/tasks?status=done")
    assert done.json()["items"] == []


@pytest.mark.asyncio
async def test_calendar_month_and_year(client, admin_conn):
    clear_dashboard_cache()
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dash3@example.com", files={}
    )
    for day in range(1, 11):
        _seed_review(
            admin_conn,
            workspace_id=workspace_id,
            kind="daily",
            period_key=f"2026-10-{day:02d}",
            title=f"2026-10-{day:02d} 每日复盘",
        )
    _seed_review(
        admin_conn, workspace_id=workspace_id, kind="monthly", period_key="2026-10",
        title="2026-10 每月复盘",
    )
    response = await client.get(f"{API}/dashboard/calendar?year=2026&month=10")
    assert response.status_code == 200, response.text
    days = response.json()["days"]
    assert len(days) == 31
    assert sum(1 for item in days if item["has_daily"]) == 10
    assert sum(1 for item in days if not item["has_daily"]) == 21

    year_view = await client.get(f"{API}/dashboard/calendar?year=2026")
    assert year_view.status_code == 200
    body = year_view.json()
    assert len(body["months"]) == 12
    assert body["days"] == []
    october = next(item for item in body["months"] if item["month"] == 10)
    assert october["has_monthly"] is True
    assert october["monthly_period_key"] == "2026-10"


@pytest.mark.asyncio
async def test_search_three_source_modes(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dash4@example.com", files=FILES
    )
    _seed_review(
        admin_conn, workspace_id=workspace_id, kind="daily", period_key="2026-10-27",
        title="2026-10-27 每日复盘", content="缓存穿透的复盘结论",
    )
    both = await client.get(f"{API}/search", params={"q": "缓存", "source_type": "both"})
    assert both.status_code == 200, both.text
    sources = {item["source_type"] for item in both.json()["items"]}
    assert sources == {"document", "review"}

    only_review = await client.get(
        f"{API}/search", params={"q": "缓存", "source_type": "review"}
    )
    items = only_review.json()["items"]
    assert items and all(item["source_type"] == "review" for item in items)
    assert all(item["review_id"] for item in items)

    only_docs = await client.get(
        f"{API}/search", params={"q": "缓存", "source_type": "document"}
    )
    items = only_docs.json()["items"]
    assert items and all(item["source_type"] == "document" for item in items)


@pytest.mark.asyncio
async def test_dashboard_stats(client, admin_conn):
    clear_dashboard_cache()
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="dash5@example.com", files={}
    )
    _seed_tasks(admin_conn, workspace_id=workspace_id, days_ago=[1, 2], done=2)
    response = await client.get(f"{API}/dashboard/stats?range=30d")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["range"] == "30d"
    assert body["tasks"]["total"] == 4
    assert body["tasks"]["completed"] == 2
    assert abs(body["completion_rate"] - 0.5) < 0.001
    assert datetime.now(UTC)  # 仅确认模块可用
