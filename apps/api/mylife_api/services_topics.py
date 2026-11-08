"""专题页状态与重渲染辅助（ARCHITECTURE §7.3；AC-40 / AC-68）。

专题 md 的实际渲染在 `packages/delivery`；本模块只负责：
- 交付后记录每个专题文件的 auto 区哈希与状态（供下次判断人工修改）；
- 把 DB 当前状态导出为交付层需要的普通 dict。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repo_delivery as repo

logger = logging.getLogger("mylife.api.topics")


async def commit_render_state(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID,
    version: int,
    hashes: dict[str, str] | None = None,
) -> None:
    """交付成功后记住 auto 区哈希。

    `hashes` 只包含本次**成功合并**的专题（`_prepare_files` 的 auto 分支）；
    因此这里必须把状态恢复为 auto —— 否则一次瞬时标记损坏留下的 manual
    会永远拒绝更新（DB 哈希停留在旧值，下一次合并必判 ManualDetected）。
    """
    if not hashes:
        return
    for topic, digest in hashes.items():
        row = await repo.get_topic_file(session, workspace_id=workspace_id, topic=topic)
        await repo.upsert_topic_file(
            session,
            workspace_id=workspace_id,
            topic=topic,
            path=row["path"] if row else f"专题/{topic}.md",
            auto_region_hash=digest,
            status="auto",
            review_id=review_id,
            version=version,
        )


async def mark_manual(
    session: AsyncSession, *, workspace_id: UUID, topic: str, reason: str
) -> None:
    row = await repo.get_topic_file(session, workspace_id=workspace_id, topic=topic)
    await repo.upsert_topic_file(
        session,
        workspace_id=workspace_id,
        topic=topic,
        path=row["path"] if row else f"专题/{topic}.md",
        auto_region_hash=(row or {}).get("auto_region_hash"),
        status="manual",
        review_id=(row or {}).get("last_review_id"),
        version=(row or {}).get("last_version"),
    )
    logger.warning("topic marked manual: %s (%s)", topic, reason)


async def topic_counts(session: AsyncSession, *, workspace_id: UUID) -> dict[str, int]:
    from mylife_api import repo_review

    counts: dict[str, int] = {}
    for slug in ("errors", "methods", "actions"):
        rows = await repo_review.list_topic_items(
            session, workspace_id=workspace_id, topic=slug, status="open"
        )
        counts[slug] = len(rows)
    return counts


def topic_item_dict(row: dict[str, Any], occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "canonical_key": row["canonical_key"],
        "title": row["title"],
        "status": row["status"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "occurrence_count": int(row["occurrence_count"]),
        "conflicts": row.get("conflicts_json") or [],
        "occurrences": [
            {
                "source_date": item["source_date"],
                "source_path": item["source_path"],
                "line_range": [item["line_start"], item["line_end"]]
                if item.get("line_start") is not None
                else None,
                "quote": item["quote"],
            }
            for item in occurrences
        ],
    }
