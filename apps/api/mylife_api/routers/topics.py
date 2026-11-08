"""专题端点（API-CONTRACTS §3.7.13；决策 4B）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from mylife_contract.errors import ErrorCode
from mylife_contract.topics import (
    ConfirmRequest,
    ResolveRequest,
    TopicItem,
    TopicItemList,
    TopicList,
    TopicSummary,
)
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repo_review
from mylife_api.deps import RunContext, get_db, get_run_context
from mylife_api.errors import AppError
from mylife_api.repositories import insert_audit

router = APIRouter(tags=["topics"])

#: API 中文 topic ↔ DB 英文枚举（PHASE3-SPEC D17）。
TOPIC_TO_SLUG = {
    "错误清单": "errors",
    "正确方法论": "methods",
    "行动清单": "actions",
    "errors": "errors",
    "methods": "methods",
    "actions": "actions",
}
SLUG_TITLES = {
    "errors": "错误清单",
    "methods": "正确方法论",
    "actions": "行动清单",
}


def _slug(topic: str) -> str:
    slug = TOPIC_TO_SLUG.get(topic)
    if slug is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return slug


def _item(row: dict, occurrences: list[dict]) -> TopicItem:
    return TopicItem(
        item_id=row["id"],
        canonical_key=row["canonical_key"],
        title=row["title"],
        status=row["status"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        occurrence_count=int(row["occurrence_count"]),
        occurrences=[
            {
                "source_date": item["source_date"],
                "source_path": item["source_path"],
                # 0/None 是无行号的哨兵（迁移 0005 的 line_start 回填）。
                "line_range": [item["line_start"], item["line_end"]]
                if item.get("line_start") and item.get("line_end") is not None
                else None,
                "quote": item["quote"],
                "evidence_id": item.get("evidence_id"),
            }
            for item in occurrences
        ],
    )


@router.get("/topics", response_model=TopicList)
async def list_topics(
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> TopicList:
    from mylife_api import repositories

    settings_row = await repositories.get_settings(db, ctx.workspace_id)
    reviews_root = (settings_row or {}).get("reviews_root") or "wiki/复盘"
    items: list[TopicSummary] = []
    for slug, title in SLUG_TITLES.items():
        rows = await repo_review.list_topic_items(
            db, workspace_id=ctx.workspace_id, topic=slug, status=None
        )
        open_count = sum(1 for row in rows if row["status"] == "open")
        last_updated = max((row["last_seen"] for row in rows), default=None)
        items.append(
            TopicSummary(
                topic=title,
                title=title,
                path=f"{reviews_root.rstrip('/')}/专题/{title}.md",
                open_count=open_count,
                last_updated=last_updated,
            )
        )
    return TopicList(items=items)


@router.get("/topics/{topic}/items", response_model=TopicItemList)
async def list_topic_items(
    topic: str,
    status_filter: str | None = Query(default=None, alias="status"),
    include_pending: bool = Query(default=False),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> TopicItemList:
    slug = _slug(topic)
    rows = await repo_review.list_topic_items(
        db,
        workspace_id=ctx.workspace_id,
        topic=slug,
        status=status_filter,
        include_pending=include_pending or status_filter == "pending",
    )
    items = []
    for row in rows:
        occurrences = await repo_review.list_topic_occurrences(
            db, workspace_id=ctx.workspace_id, item_id=row["id"]
        )
        items.append(_item(row, occurrences))
    return TopicItemList(items=items)


@router.post(
    "/topics/{topic}/items/{item_id}/confirm",
    response_model=TopicItem,
)
async def confirm_topic_item(
    topic: str,
    item_id: UUID,
    payload: ConfirmRequest,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> TopicItem:
    slug = _slug(topic)
    if ctx.role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    row = await repo_review.get_topic_item(
        db, workspace_id=ctx.workspace_id, item_id=item_id
    )
    if row is None or row["topic"] != slug:
        raise AppError(ErrorCode.NOT_FOUND)
    if row["status"] != "pending":
        raise AppError(ErrorCode.CONFLICT, details={"status": row["status"]})

    target = "open" if payload.decision == "accept" else "dropped"
    await repo_review.update_topic_status(
        db, workspace_id=ctx.workspace_id, item_id=item_id, status=target
    )
    # D9：人工确认时可修正抽取结果；白名单（title + 条目字段）写回
    # fields_json，优先级高于物化值。
    edited = payload.edited_fields or {}
    if edited:
        allowed = {"title", "event", "name", "text", "root_cause", "cost",
                   "correct_action", "recurrence_trigger", "source",
                   "applicable_scenario", "steps", "boundaries",
                   "landing_action", "verification_criteria", "trigger",
                   "verification", "summary"}
        fields = {key: value for key, value in edited.items() if key in allowed}
        if fields:
            await repo_review.merge_topic_fields(
                db,
                workspace_id=ctx.workspace_id,
                item_id=item_id,
                fields=fields,
            )
    await insert_audit(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        action="topic.confirmed",
        target=str(item_id),
        detail={
            "topic": slug,
            "decision": payload.decision,
            "edited_fields": sorted((payload.edited_fields or {}).keys()),
        },
    )
    updated = await repo_review.get_topic_item(
        db, workspace_id=ctx.workspace_id, item_id=item_id
    )
    occurrences = await repo_review.list_topic_occurrences(
        db, workspace_id=ctx.workspace_id, item_id=item_id
    )
    return _item(updated, occurrences)


@router.post(
    "/topics/{topic}/items/{item_id}/resolve",
    response_model=TopicItem,
)
async def resolve_topic_item(
    topic: str,
    item_id: UUID,
    payload: ResolveRequest,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> TopicItem:
    slug = _slug(topic)
    if ctx.role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    row = await repo_review.get_topic_item(
        db, workspace_id=ctx.workspace_id, item_id=item_id
    )
    if row is None or row["topic"] != slug:
        raise AppError(ErrorCode.NOT_FOUND)
    if row["status"] == "resolved":
        raise AppError(ErrorCode.CONFLICT, details={"status": "resolved"})
    if row["status"] == "pending":
        # 未被确认的条目不能直接解决（决策 4B）。
        raise AppError(ErrorCode.CONFLICT, details={"status": "pending"})

    await repo_review.update_topic_status(
        db, workspace_id=ctx.workspace_id, item_id=item_id, status="resolved"
    )
    await insert_audit(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        action="topic.resolved",
        target=str(item_id),
        detail={"topic": slug, "note": payload.note},
    )
    updated = await repo_review.get_topic_item(
        db, workspace_id=ctx.workspace_id, item_id=item_id
    )
    occurrences = await repo_review.list_topic_occurrences(
        db, workspace_id=ctx.workspace_id, item_id=item_id
    )
    return _item(updated, occurrences)


__all__ = ["router"]
