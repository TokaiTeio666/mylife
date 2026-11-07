"""Todo 台账写入与重建（AC-31/32/33；ARCHITECTURE §6）。

- occurrence 由 `ParsedDocument.task_occurrences` 写入；重导入 delete+reinsert。
- `tasks` 完全由 occurrence 确定性重建（first_seen/last_seen/done_date/source）。
- `carried` 不落库，按 `status='open' AND first_seen < period_start` 派生。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

from mylife_agent.task_ledger import task_key
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_worker.ids import uuid7

logger = logging.getLogger("mylife.worker.task_ledger")


async def sync_document_occurrences(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    document_id: UUID,
    occurred_on: date | None,
    occurrences: list[Any],
) -> int:
    """删除该文档的旧 occurrence，再写入本次解析结果；返回写入行数。

    `occurred_on` 为 NULL 时不写 occurrence（DATA-MODEL §4.7 要求 NOT NULL），
    由调用方计入 `tasks_skipped_no_date`。
    """
    await session.execute(
        text(
            "DELETE FROM task_occurrences "
            "WHERE workspace_id = :ws AND document_id = :doc"
        ),
        {"ws": workspace_id, "doc": document_id},
    )
    if occurred_on is None or not occurrences:
        return 0

    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for item in occurrences:
        key = task_key(item.text)
        if not key:
            continue
        marker = (item.line_number, key)
        if marker in seen:  # 同一行同一文本只保留一条，避免唯一键冲突
            continue
        seen.add(marker)
        rows.append(
            {
                "id": uuid7(),
                "ws": workspace_id,
                "doc": document_id,
                "date": occurred_on,
                "checked": bool(item.checked),
                "line": item.line_number,
                "raw": item.text,
                "key": key,
            }
        )
    if not rows:
        return 0

    # 任务行必须先存在，才能写 occurrence（task_id NOT NULL）。
    await session.execute(
        text(
            "INSERT INTO tasks (id, workspace_id, key, text, status, first_seen, last_seen) "
            "SELECT :id, :ws, :key, :raw, 'open', :date, :date "
            "ON CONFLICT (workspace_id, key) DO NOTHING"
        ),
        [
            {
                "id": row["id"],
                "ws": row["ws"],
                "key": row["key"],
                "raw": row["raw"],
                "date": row["date"],
            }
            for row in rows
        ],
    )
    task_ids = await _task_ids_for_keys(session, workspace_id, [row["key"] for row in rows])

    await session.execute(
        text(
            "INSERT INTO task_occurrences "
            "(id, workspace_id, task_id, document_id, occurrence_date, checked, "
            " line_number, raw_text) "
            "VALUES (:id, :ws, :task, :doc, :date, :checked, :line, :raw) "
            "ON CONFLICT (workspace_id, document_id, occurrence_date, line_number) DO NOTHING"
        ),
        [
            {
                "id": row["id"],
                "ws": row["ws"],
                "task": task_ids[row["key"]],
                "doc": row["doc"],
                "date": row["date"],
                "checked": row["checked"],
                "line": row["line"],
                "raw": row["raw"],
            }
            for row in rows
        ],
    )
    return len(rows)


async def _task_ids_for_keys(
    session: AsyncSession, workspace_id: UUID, keys: list[str]
) -> dict[str, UUID]:
    if not keys:
        return {}
    result = await session.execute(
        text("SELECT key, id FROM tasks WHERE workspace_id = :ws AND key = ANY(:keys)"),
        {"ws": workspace_id, "keys": list(set(keys))},
    )
    return {row.key: row.id for row in result}


async def rebuild_tasks(session: AsyncSession, *, workspace_id: UUID) -> int:
    """由 occurrence 确定性重建 tasks；返回受影响行数。"""
    await session.execute(
        text(
            """
            WITH agg AS (
              SELECT task_id,
                     min(occurrence_date) AS first_seen,
                     max(occurrence_date) AS last_seen,
                     max(occurrence_date) FILTER (WHERE checked) AS last_done
              FROM task_occurrences
              WHERE workspace_id = :ws
              GROUP BY task_id
            ), latest AS (
              SELECT DISTINCT ON (o.task_id)
                     o.task_id, o.raw_text, o.checked, d.path, o.line_number
              FROM task_occurrences o
              JOIN documents d ON d.id = o.document_id
              WHERE o.workspace_id = :ws
              ORDER BY o.task_id, o.occurrence_date DESC, d.path ASC, o.line_number ASC
            )
            UPDATE tasks t
            SET first_seen = agg.first_seen,
                last_seen = agg.last_seen,
                status = CASE WHEN latest.checked THEN 'done' ELSE 'open' END,
                done_date = CASE WHEN latest.checked THEN agg.last_done ELSE NULL END,
                text = latest.raw_text,
                source_path = latest.path,
                line_number = latest.line_number
            FROM agg JOIN latest USING (task_id)
            WHERE t.id = agg.task_id AND t.workspace_id = :ws
            """
        ),
        {"ws": workspace_id},
    )
    result = await session.execute(
        text(
            "DELETE FROM tasks t WHERE t.workspace_id = :ws "
            "AND NOT EXISTS (SELECT 1 FROM task_occurrences o WHERE o.task_id = t.id)"
        ),
        {"ws": workspace_id},
    )
    return int(result.rowcount or 0)


async def delete_document_occurrences(
    session: AsyncSession, *, workspace_id: UUID, document_id: UUID
) -> None:
    await session.execute(
        text(
            "DELETE FROM task_occurrences "
            "WHERE workspace_id = :ws AND document_id = :doc"
        ),
        {"ws": workspace_id, "doc": document_id},
    )
