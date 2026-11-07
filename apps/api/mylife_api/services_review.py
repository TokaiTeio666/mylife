"""复盘服务：准入、查询、审批、版本（API-CONTRACTS §3.7）。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from mylife_contract.common import is_valid_period_key
from mylife_contract.constants import REVISION_LIMIT
from mylife_contract.errors import ErrorCode
from mylife_contract.review import (
    ReviewCreateRequest,
    ReviewCreateResponse,
    ReviewDetail,
    ReviewList,
    ReviewSummary,
    VersionRef,
)
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repo_review as repo
from mylife_api import repositories as repo0
from mylife_api import services_budget
from mylife_api.context import current_request_id
from mylife_api.db import get_session_factory, set_rls_context
from mylife_api.errors import AppError
from mylife_api.repositories import insert_audit
from mylife_api.review_runner import run_review

logger = logging.getLogger("mylife.api.review")

REVIEW_CONCURRENCY = 2
_tasks: set[asyncio.Task[Any]] = set()


def _title(kind: str, period_key: str) -> str:
    if kind == "daily":
        return f"{period_key} 每日复盘"
    if kind == "weekly":
        return f"{period_key} 每周复盘"
    return f"{period_key} 年度复盘"


def _summary(row: dict[str, Any]) -> ReviewSummary:
    return ReviewSummary(
        id=row["id"],
        kind=row["kind"],
        period_key=row["period_key"],
        title=row["title"],
        version=int(row["version"]),
        status=row["status"],
        generated_at=row["generated_at"],
        path=row.get("git_path"),
        degraded=bool(row.get("degraded")),
        delivery_status="none",
    )


def _validate_period(payload: ReviewCreateRequest, *, timezone: str, now: datetime) -> date:
    if not is_valid_period_key(payload.kind, payload.period_key):
        raise AppError(ErrorCode.REVIEW_PERIOD_INVALID, details={"field": "period_key"})
    if payload.options and payload.options.max_revisions is not None:
        limit = REVISION_LIMIT[payload.kind]
        if payload.options.max_revisions > limit:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                details={"field": "options.max_revisions", "limit": limit},
            )
    if payload.kind == "daily":
        target = date.fromisoformat(payload.period_key)
        local_today = now.astimezone(_zone(timezone)).date()
        if target > local_today:
            raise AppError(ErrorCode.REVIEW_PERIOD_INVALID, details={"reason": "FUTURE_PERIOD"})
        return target
    if payload.kind == "yearly":
        return date(int(payload.period_key), 1, 1)
    year, week = payload.period_key.split("-W")
    try:
        return date.fromisocalendar(int(year), int(week), 1)
    except ValueError as exc:
        raise AppError(ErrorCode.REVIEW_PERIOD_INVALID) from exc


def _zone(name: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Shanghai")


async def _has_source_data(
    session: AsyncSession, *, workspace_id: UUID, kind: str, period_key: str
) -> bool:
    from sqlalchemy import text

    if kind == "daily":
        result = await session.execute(
            text(
                "SELECT (SELECT count(*) FROM documents WHERE workspace_id = :ws "
                "        AND occurred_on = CAST(:period AS date)) + "
                "       (SELECT count(*) FROM task_occurrences WHERE workspace_id = :ws "
                "        AND occurrence_date = CAST(:period AS date)) + "
                "       (SELECT count(*) FROM document_changes WHERE workspace_id = :ws "
                "        AND source_date = CAST(:period AS date)) AS n"
            ),
            {"ws": workspace_id, "period": period_key},
        )
    else:
        result = await session.execute(
            text(
                "SELECT count(*) FROM reviews WHERE workspace_id = :ws "
                "AND kind = :kind AND status IN ('delivered','approved')"
            ),
            {"ws": workspace_id, "kind": "daily" if kind == "weekly" else "weekly"},
        )
    return int(result.scalar_one()) > 0


async def create_review(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
    payload: ReviewCreateRequest,
) -> tuple[ReviewCreateResponse, int]:
    """准入；返回 (response, http_status)。"""
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    settings_row = await repo0.get_settings(session, workspace_id)
    timezone = payload.timezone or (settings_row or {}).get("timezone") or "Asia/Shanghai"

    await services_budget.enforce_review_quota(session, workspace_id=workspace_id, user_id=user_id)
    _validate_period(payload, timezone=timezone, now=datetime.now(UTC))

    # AC-25：带 confirmation_token 时必须已 confirm 且未被复用（D6 裁决）。
    if payload.confirmation_token:
        from mylife_api import confirmations

        try:
            consumed = await confirmations.is_consumed(
                session,
                token=payload.confirmation_token,
                workspace_id=workspace_id,
                user_id=user_id,
                kind=payload.kind,
                period_key=payload.period_key,
            )
        except confirmations.TokenError as exc:
            raise AppError(
                ErrorCode.VALIDATION_ERROR, details={"reason": exc.reason}
            ) from exc
        if not consumed:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                details={"reason": "CONFIRMATION_REQUIRED"},
            )

    await repo.lock_period(
        session, workspace_id=workspace_id, kind=payload.kind, period_key=payload.period_key
    )
    active = await repo.find_active_review_run(
        session, workspace_id=workspace_id, kind=payload.kind, period_key=payload.period_key
    )
    if active is not None:
        raise AppError(
            ErrorCode.ACTIVE_RUN_EXISTS,
            details={"existing_run_id": str(active["id"]), "state": active["state"]},
        )
    if (
        await repo.count_active_review_runs(session, workspace_id=workspace_id)
        >= REVIEW_CONCURRENCY
    ):
        raise AppError(
            ErrorCode.ACTIVE_RUN_EXISTS,
            details={"reason": "REVIEW_CONCURRENCY_LIMIT"},
        )

    has_data = await _has_source_data(
        session, workspace_id=workspace_id, kind=payload.kind, period_key=payload.period_key
    )
    if not has_data and not payload.allow_empty:
        raise AppError(
            ErrorCode.REVIEW_NO_SOURCE_DATA,
            details={
                "period_key": payload.period_key,
                "reason": "no_documents_no_tasks_no_journal",
            },
        )

    await services_budget.check_workspace_quota(
        session, workspace_id=workspace_id, timezone=timezone
    )

    existing = await repo.get_review_by_period(
        session, workspace_id=workspace_id, kind=payload.kind, period_key=payload.period_key
    )
    snapshot = await repo.latest_snapshot(session, workspace_id=workspace_id)
    snapshot_id = snapshot["id"] if snapshot else None

    if existing is not None:
        version = await repo.next_review_version(
            session, workspace_id=workspace_id, review_id=existing["id"]
        )
        review_id = existing["id"]
        http_status = 200
    else:
        version = 1
        created = await repo.insert_review(
            session,
            workspace_id=workspace_id,
            kind=payload.kind,
            period_key=payload.period_key,
            title=_title(payload.kind, payload.period_key),
            version=version,
            snapshot_id=snapshot_id,
        )
        review_id = created["id"]
        http_status = 201

    run = await repo.insert_review_run(
        session,
        workspace_id=workspace_id,
        kind=payload.kind,
        period_key=payload.period_key,
        review_id=review_id,
        version=version,
        state_payload={
            "review_id": str(review_id),
            "user_id": str(user_id),
            "timezone": timezone,
            "trigger": payload.trigger,
            "allow_empty": payload.allow_empty,
            "options": payload.options.model_dump() if payload.options else {},
            "snapshot_id": str(snapshot_id) if snapshot_id else None,
            "attempts": {},
        },
        trace_id=current_request_id(),
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="review.created",
        target=str(review_id),
        detail={
            "kind": payload.kind,
            "period_key": payload.period_key,
            "trigger": payload.trigger,
            "version": version,
        },
    )
    await session.commit()
    await set_rls_context(session, workspace_id, user_id)

    task = asyncio.create_task(run_review(run["id"], workspace_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

    response = ReviewCreateResponse(
        run_id=run["id"],
        review_id=review_id,
        state="created",
        existing_run_id=None,
        version=version,
    )
    return response, http_status


async def list_reviews(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    kind: str | None,
    status: str | None,
    limit: int,
    cursor: str | None,
) -> ReviewList:
    from mylife_api.pagination import decode_review_cursor, encode_review_cursor

    after = decode_review_cursor(cursor)
    rows = await repo.list_reviews(
        session,
        workspace_id=workspace_id,
        kind=kind,
        status=status,
        limit=limit + 1,
        after=after,
    )
    has_more = len(rows) > limit
    items = [_summary(row) for row in rows[:limit]]
    next_cursor = None
    if has_more and rows:
        last = rows[limit - 1]
        next_cursor = encode_review_cursor(last["period_key"], int(last["version"]))
    return ReviewList(items=items, next_cursor=next_cursor, has_more=has_more)


async def get_review_detail(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID,
    version: int | None = None,
) -> ReviewDetail:
    from mylife_api.rendering import render_markdown

    row = await repo.get_review(session, workspace_id=workspace_id, review_id=review_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND)
    current_version = int(row["version"])
    target_version = version or current_version

    version_row = await repo.get_version(
        session, workspace_id=workspace_id, review_id=review_id, version=target_version
    )
    if version_row is None and target_version != current_version:
        raise AppError(ErrorCode.REVIEW_VERSION_NOT_FOUND)
    content_md = version_row["content_md"] if version_row else row["content"]

    sections = await repo.list_sections(
        session, workspace_id=workspace_id, review_id=review_id, version=target_version
    )
    entries = await repo.list_entries(
        session, workspace_id=workspace_id, review_id=review_id, version=target_version
    )
    citations = await repo.list_citations(
        session, workspace_id=workspace_id, review_id=review_id, version=target_version
    )
    versions = await repo.list_versions(session, workspace_id=workspace_id, review_id=review_id)

    return ReviewDetail(
        id=row["id"],
        kind=row["kind"],
        period_key=row["period_key"],
        version=target_version,
        current_version=current_version,
        title=row["title"],
        content_md=content_md,
        content_html=render_markdown(content_md, document_id=str(review_id)),
        sections=[
            {
                "section_key": item["section_key"],
                "ordinal": item["ordinal"],
                "content_md": item["content_md"],
                "structured": item["structured_json"] or {},
            }
            for item in sections
        ],
        citations=[
            {
                "claim_id": item["claim_id"],
                "evidence_id": item["evidence_id"],
                "source_path": item["source_path"],
                "source_date": item["source_date"],
                "line_range": [item["line_start"], item["line_end"]]
                if item.get("line_start") is not None
                else None,
                "quote": item["quote"],
            }
            for item in citations
        ],
        entries=[
            {
                "section_key": item["section_key"],
                "item_type": item["item_type"],
                "canonical_key": item["canonical_key"],
                "fields": item["fields_json"] or {},
                "source_path": item["source_path"],
                "line_range": [item["line_start"], item["line_end"]]
                if item.get("line_start") is not None
                else None,
                "confidence": float(item["confidence"]) if item.get("confidence") else None,
            }
            for item in entries
        ],
        verdict=row.get("verdict_json"),
        model=row.get("model"),
        snapshot_id=row.get("snapshot_id"),
        generated_at=row["generated_at"],
        degraded=bool(row.get("degraded")),
        degraded_reasons=list(row.get("degraded_reasons") or []),
        git_path=row.get("git_path"),
        versions=[
            VersionRef(
                version=item["version"],
                content_hash=item["content_hash"],
                git_sha=item.get("git_sha"),
                generated_at=item["created_at"],
                is_current=int(item["version"]) == current_version,
            )
            for item in versions
        ],
        superseded_by=None,
    )


async def approve_run(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    user_id: UUID,
    role: str,
    note: str | None,
    edited_content_md: str | None,
) -> dict[str, Any]:
    run = await repo.get_run(session, workspace_id=workspace_id, run_id=run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND)
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    if run["state"] == "security_abort":
        raise AppError(ErrorCode.SECURITY_ABORT)
    if run["state"] != "human_gate":
        raise AppError(ErrorCode.CONFLICT, details={"state": run["state"]})

    if edited_content_md:
        # 编辑后必须重新过门禁：Phase 3 仅拒绝绕过（完整重评审见 Phase 4）。
        raise AppError(
            ErrorCode.REVIEW_GATE_FAILED,
            details={"reason": "EDITED_CONTENT_REQUIRES_REVIEW"},
        )

    from mylife_api.runtime import STREAM_EPOCH

    ok = await repo.cas_transition(
        session,
        workspace_id=workspace_id,
        run_id=run_id,
        from_state="human_gate",
        to_state="delivering",
        reason="human_approved",
        stream_epoch=STREAM_EPOCH,
    )
    if not ok:
        raise AppError(ErrorCode.REVIEW_ALREADY_DECIDED)
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="review.approved",
        target=str(run_id),
        detail={"note": note, "edited": False},
    )
    await session.commit()
    await set_rls_context(session, workspace_id, user_id)

    task = asyncio.create_task(run_review(run_id, workspace_id, resume_from="delivering"))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"run_id": run_id, "state": "delivering", "delivery_id": None}


async def reject_run(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    user_id: UUID,
    role: str,
    reason_code: str,
    note: str | None,
) -> dict[str, Any]:
    run = await repo.get_run(session, workspace_id=workspace_id, run_id=run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND)
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    if run["state"] != "human_gate":
        raise AppError(ErrorCode.CONFLICT, details={"state": run["state"]})

    from mylife_api.runtime import STREAM_EPOCH

    ok = await repo.cas_transition(
        session,
        workspace_id=workspace_id,
        run_id=run_id,
        from_state="human_gate",
        to_state="cancelled",
        reason="human_rejected",
        stream_epoch=STREAM_EPOCH,
    )
    if not ok:
        raise AppError(ErrorCode.REVIEW_ALREADY_DECIDED)
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="review.rejected",
        target=str(run_id),
        detail={"reason_code": reason_code, "note": note},
    )
    await session.commit()
    await set_rls_context(session, workspace_id, user_id)
    return {"run_id": run_id, "state": "cancelled"}


async def cancel_run(
    session: AsyncSession, *, workspace_id: UUID, run_id: UUID, user_id: UUID
) -> dict[str, Any]:
    run = await repo.get_run(session, workspace_id=workspace_id, run_id=run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND)
    if run["state"] not in {"created", "snapshotting", "planning"}:
        raise AppError(ErrorCode.FORBIDDEN, details={"state": run["state"]})

    from mylife_api.runtime import STREAM_EPOCH

    ok = await repo.cas_transition(
        session,
        workspace_id=workspace_id,
        run_id=run_id,
        from_state=run["state"],
        to_state="cancelled",
        reason="user_cancel",
        stream_epoch=STREAM_EPOCH,
    )
    if not ok:
        raise AppError(ErrorCode.CONFLICT)
    await session.commit()
    await set_rls_context(session, workspace_id, user_id)
    return {"run_id": run_id, "state": "cancelled"}


async def wait_for_run(workspace_id: UUID, run_id: UUID, *, wait_seconds: float = 30.0) -> str:
    """测试与脚本用：等待复盘 run 进入终态。"""
    import time

    from mylife_api.review_fsm import TERMINAL_STATES

    factory = get_session_factory()
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        async with factory() as session:
            await set_rls_context(session, workspace_id, None)
            run = await repo.get_run(session, workspace_id=workspace_id, run_id=run_id)
            if run is not None and run["state"] in TERMINAL_STATES:
                return run["state"]
            await session.rollback()
        await asyncio.sleep(0.05)
    raise TimeoutError(f"review run {run_id} did not finish")


def decode_state_payload(run: dict[str, Any]) -> dict[str, Any]:
    payload = run.get("state_payload") or {}
    if isinstance(payload, str):
        return json.loads(payload)
    return payload
