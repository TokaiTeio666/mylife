"""Chat 服务：会话 CRUD、turn 准入、run 查询/取消、SSE。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from mylife_contract.chat import (
    ChatTurnRequest,
    ChatTurnResponse,
    ConversationList,
    ConversationSummary,
    CreateConversationRequest,
    Message,
    MessageList,
    MessageUsage,
    Reference,
    RunDetail,
    RunStepView,
    StreamInfo,
    ToolEvent,
    UpdateConversationRequest,
)
from mylife_contract.errors import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repo_chat as repo
from mylife_api import repositories as repo0
from mylife_api.chat_runner import TERMINAL_STATES, run_chat
from mylife_api.context import current_request_id
from mylife_api.db import get_session_factory, set_rls_context
from mylife_api.errors import AppError
from mylife_api.ids import uuid7
from mylife_api.ratelimit import get_limiter
from mylife_api.runbus import bus
from mylife_api.runtime import STREAM_EPOCH

logger = logging.getLogger("mylife.api.chat")

CONVERSATION_LIMIT = 5000
TURN_RATE_LIMIT_WORKSPACE = 20
TURN_RATE_WINDOW = 60
TURN_QUOTA_USER = 200
TURN_QUOTA_WINDOW = 86400

_tasks: set[asyncio.Task[Any]] = set()


def _summary(row: dict[str, Any]) -> ConversationSummary:
    return ConversationSummary(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=int(row.get("message_count") or 0),
        last_message_preview=(row.get("last_message_preview") or "")[:120],
    )


async def list_conversations(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    q: str | None,
    limit: int,
) -> ConversationList:
    if q is not None and len(q) > 100:
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "q"})
    rows = await repo.list_conversations(
        session, workspace_id=workspace_id, user_id=user_id, q=q, limit=limit
    )
    return ConversationList(items=[_summary(row) for row in rows])


async def create_conversation(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    payload: CreateConversationRequest,
) -> ConversationSummary:
    if await repo.count_conversations(session, workspace_id) >= CONVERSATION_LIMIT:
        raise AppError(ErrorCode.QUOTA_EXCEEDED)
    title = (payload.title or "新对话").strip()
    row = await repo.insert_conversation(
        session, workspace_id=workspace_id, user_id=user_id, title=title
    )
    return _summary(row)


async def get_conversation_summary(
    session: AsyncSession, *, workspace_id: UUID, user_id: UUID, conversation_id: UUID
) -> ConversationSummary:
    row = await repo.get_conversation_summary(
        session, workspace_id=workspace_id, conversation_id=conversation_id, user_id=user_id
    )
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return _summary(row)


async def rename_conversation(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    payload: UpdateConversationRequest,
) -> ConversationSummary:
    exists = await repo.get_conversation(
        session, workspace_id=workspace_id, conversation_id=conversation_id, user_id=user_id
    )
    if exists is None:
        raise AppError(ErrorCode.NOT_FOUND)
    await repo.rename_conversation(
        session, conversation_id=conversation_id, title=payload.title.strip()
    )
    return await get_conversation_summary(
        session, workspace_id=workspace_id, user_id=user_id, conversation_id=conversation_id
    )


async def delete_conversation(
    session: AsyncSession, *, workspace_id: UUID, user_id: UUID, conversation_id: UUID
) -> None:
    exists = await repo.get_conversation(
        session, workspace_id=workspace_id, conversation_id=conversation_id, user_id=user_id
    )
    if exists is None:
        raise AppError(ErrorCode.NOT_FOUND)
    for run in await repo.list_active_conversation_runs(
        session, workspace_id=workspace_id, conversation_id=conversation_id
    ):
        try:
            await _cancel_run(session, workspace_id, run["id"], reason="conversation_deleted")
        except AppError as exc:
            if exc.code != ErrorCode.CONFLICT:
                raise
    await repo.soft_delete_conversation(session, conversation_id)


def _message(row: dict[str, Any]) -> Message:
    references = [
        Reference.model_validate(item) for item in (row.get("references_json") or [])
    ]
    tool_events = [
        ToolEvent.model_validate(item) for item in (row.get("tool_events_json") or [])
    ]
    usage_raw = row.get("usage_json")
    usage = MessageUsage.model_validate(usage_raw) if usage_raw else None
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        content=row["content"],
        status=row["status"],
        references=references,
        tool_events=tool_events,
        usage=usage,
        run_id=row.get("run_id"),
        created_at=row["created_at"],
    )


async def list_messages(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    limit: int,
) -> MessageList:
    exists = await repo.get_conversation(
        session, workspace_id=workspace_id, conversation_id=conversation_id, user_id=user_id
    )
    if exists is None:
        raise AppError(ErrorCode.NOT_FOUND)
    rows = await repo.list_messages(session, conversation_id=conversation_id, limit=limit)
    return MessageList(items=[_message(row) for row in rows])


async def _cancel_run(
    session: AsyncSession,
    workspace_id: UUID,
    run_id: UUID,
    *,
    reason: str | None = None,
) -> None:
    run = await repo.get_run(session, workspace_id=workspace_id, run_id=run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND)
    if run["state"] in TERMINAL_STATES:
        raise AppError(ErrorCode.CONFLICT)
    payload = run.get("state_payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    await repo.update_run_state(
        session, workspace_id=workspace_id, run_id=run_id, state="cancelled", finished=True
    )
    await repo.insert_event(
        session,
        workspace_id=workspace_id,
        run_id=run_id,
        event="chat.cancelled",
        payload={"by": "user", "reason": reason},
        from_state=run["state"],
        to_state="cancelled",
    )
    assistant_message_id = payload.get("assistant_message_id")
    if assistant_message_id:
        await repo.update_message_status(session, UUID(assistant_message_id), "cancelled")


async def cancel_run(
    session: AsyncSession, *, workspace_id: UUID, run_id: UUID, user_id: UUID | None = None
) -> dict[str, Any]:
    run = await repo.get_run(session, workspace_id=workspace_id, run_id=run_id)
    if run is not None and run["kind"] != "chat":
        # 复盘 run 只在 created/snapshotting/planning 可取消，之后需走 reject。
        from mylife_api import services_review

        if user_id is None:
            raise AppError(ErrorCode.FORBIDDEN)
        return await services_review.cancel_run(
            session, workspace_id=workspace_id, run_id=run_id, user_id=user_id
        )
    await _cancel_run(session, workspace_id, run_id)
    return {"run_id": run_id, "state": "cancelled"}


async def _workspace_defaults(session: AsyncSession, workspace_id: UUID) -> dict[str, Any]:
    row = await repo0.get_settings(session, workspace_id)
    if row is None:
        return {"retrieval_top_k": 20, "context_window_tokens": 32000, "web_search_enabled": False}
    return {
        "retrieval_top_k": row["retrieval_top_k"],
        "context_window_tokens": row["context_window_tokens"],
        "web_search_enabled": row["web_search_enabled"],
    }


async def create_turn(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    payload: ChatTurnRequest,
) -> tuple[ChatTurnResponse, bool]:
    conversation = await repo.get_conversation(
        session, workspace_id=workspace_id, conversation_id=conversation_id, user_id=user_id
    )
    if conversation is None:
        raise AppError(ErrorCode.NOT_FOUND)

    defaults = await _workspace_defaults(session, workspace_id)
    options = {
        "retrieval_top_k": (
            payload.options.retrieval_top_k
            if payload.options and payload.options.retrieval_top_k
            else defaults["retrieval_top_k"]
        ),
        "context_window_tokens": (
            payload.options.context_window_tokens
            if payload.options and payload.options.context_window_tokens
            else defaults["context_window_tokens"]
        ),
        "web_search_enabled": bool(
            defaults["web_search_enabled"]
            or (payload.options.web_search_enabled if payload.options else False)
        ),
        "model_profile": payload.options.model_profile if payload.options else "default",
    }
    # MVP：Web 搜索默认关闭，请求显式开启直接拒绝（AC-30）。
    if payload.options is not None and payload.options.web_search_enabled:
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"reason": "FEATURE_DISABLED"})
    options["web_search_enabled"] = False

    limiter = get_limiter()
    ws_limit = await limiter.hit(
        f"rl:chat:ws:{workspace_id}", TURN_RATE_LIMIT_WORKSPACE, TURN_RATE_WINDOW
    )
    if not ws_limit.allowed:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            headers={"Retry-After": str(max(1, ws_limit.reset_at - int(time.time())))},
        )
    user_quota = await limiter.hit(
        f"quota:chat:user:{user_id}", TURN_QUOTA_USER, TURN_QUOTA_WINDOW
    )
    if not user_quota.allowed:
        raise AppError(ErrorCode.QUOTA_EXCEEDED)

    existing = await repo.find_turn_run(
        session,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        client_turn_id=payload.client_turn_id,
    )
    if existing is not None:
        after = await repo.max_event_id(
            session, workspace_id=workspace_id, run_id=existing["id"]
        )
        state_payload = existing.get("state_payload") or {}
        if isinstance(state_payload, str):
            state_payload = json.loads(state_payload)
        assistant_id = state_payload.get("assistant_message_id")
        response = ChatTurnResponse(
            run_id=existing["id"],
            message_id=UUID(assistant_id) if assistant_id else uuid7(),
            stream=StreamInfo(
                events_url=f"/api/v1/runs/{existing['id']}/events",
                epoch=STREAM_EPOCH,
                after=after,
            ),
        )
        return response, True

    for active in await repo.list_active_conversation_runs(
        session, workspace_id=workspace_id, conversation_id=conversation_id
    ):
        await _cancel_run(session, workspace_id, active["id"], reason="superseded")

    run = await repo.insert_chat_run(
        session,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        client_turn_id=payload.client_turn_id,
        state_payload={
            "question": payload.message,
            "user_id": str(user_id),
            "options": options,
        },
        trace_id=current_request_id(),
    )

    user_message = await repo.insert_message(
        session,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        role="user",
        content=payload.message,
        status="complete",
        run_id=run["id"],
    )
    assistant_message = await repo.insert_message(
        session,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        role="assistant",
        content="",
        status="drafting",
        run_id=run["id"],
    )
    await repo.update_run_state(
        session,
        workspace_id=workspace_id,
        run_id=run["id"],
        state="created",
        state_payload={
            "question": payload.message,
            "user_id": str(user_id),
            "options": options,
            "assistant_message_id": str(assistant_message["id"]),
            "user_message_id": str(user_message["id"]),
        },
    )
    await repo.touch_conversation(session, conversation_id)
    if conversation["title"] == "新对话":
        title = payload.message.strip()[:40] or "新对话"
        await repo.rename_conversation(session, conversation_id=conversation_id, title=title)

    # 先提交，确保后台 runner 与 SSE 能看到 run/messages。
    await session.commit()
    await set_rls_context(session, workspace_id, user_id)

    task = asyncio.create_task(run_chat(run["id"], workspace_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

    response = ChatTurnResponse(
        run_id=run["id"],
        message_id=assistant_message["id"],
        stream=StreamInfo(
            events_url=f"/api/v1/runs/{run['id']}/events",
            epoch=STREAM_EPOCH,
            after=0,
        ),
    )
    return response, False


async def get_run_detail(
    session: AsyncSession, *, workspace_id: UUID, run_id: UUID
) -> RunDetail:
    run = await repo.get_run(session, workspace_id=workspace_id, run_id=run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND)
    steps = await repo.list_steps(session, run_id)
    return RunDetail(
        run_id=run["id"],
        kind=run["kind"],
        state=run["state"],
        period_key=run.get("period_key"),
        conversation_id=run.get("conversation_id"),
        attempt_id=run.get("attempt_id"),
        retry_from_state=run.get("retry_from_state"),
        snapshot_id=run.get("snapshot_id"),
        degraded=False,
        degraded_reasons=[],
        steps=[
            RunStepView(
                seq=step["seq"],
                role=step["role"],
                tool_name=step.get("tool_name"),
                status=step["status"],
                model=step.get("model"),
                tokens=int(step.get("tokens") or 0),
                latency_ms=step.get("latency_ms"),
            )
            for step in steps
        ],
        created_at=run["created_at"],
        started_at=run.get("updated_at"),
        finished_at=None,
    )


async def wait_for_run(
    workspace_id: UUID, run_id: UUID, *, wait_seconds: float = 20.0
) -> str:
    """测试与脚本用：等待 run 进入终态。"""
    factory = get_session_factory()
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        async with factory() as session:
            await set_rls_context(session, workspace_id, None)
            run = await repo.get_run(
                session, workspace_id=workspace_id, run_id=run_id
            )
            if run is not None and run["state"] in TERMINAL_STATES:
                return run["state"]
            await session.rollback()
        await asyncio.sleep(0.05)
    raise TimeoutError(f"run {run_id} did not finish")


def _frame(event_id: int | None, event: str, payload: dict[str, Any]) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


async def stream_run_events(
    *, workspace_id: UUID, run_id: UUID, after: int, epoch: int
) -> AsyncIterator[str]:
    if epoch and epoch != STREAM_EPOCH:
        yield _frame(
            None,
            "degraded",
            {"reason": "epoch_mismatch", "scope": "sse", "detail": "请从 run 状态重建"},
        )
        return

    yield "retry: 3000\n\n"
    factory = get_session_factory()

    async def fetch(new_after: int) -> list[dict[str, Any]]:
        async with factory() as session:
            await set_rls_context(session, workspace_id, None)
            rows = await repo.list_events(
                session, workspace_id=workspace_id, run_id=run_id, after=new_after
            )
            await session.rollback()
            return rows

    async def run_state() -> str | None:
        async with factory() as session:
            await set_rls_context(session, workspace_id, None)
            run = await repo.get_run(session, workspace_id=workspace_id, run_id=run_id)
            await session.rollback()
            return run["state"] if run else None

    last = after
    terminal_seen = False
    last_heartbeat = time.monotonic()

    async with bus.subscribe(run_id) as queue:
        while True:
            rows = await fetch(last)
            for row in rows:
                if row["event_id"] <= last:
                    continue
                last = row["event_id"]
                yield _frame(last, row["event"], row["payload"])
                if row["event"] in {
                    "chat.done",
                    "done",
                    "chat.error",
                    "error",
                    "chat.cancelled",
                    "cancelled",
                }:
                    terminal_seen = True

            while not queue.empty():
                frame = queue.get_nowait()
                if frame.get("id") is None:
                    yield _frame(None, frame["event"], frame["data"])
                # 持久化事件由上面的 DB 回放负责，避免重复

            if terminal_seen:
                return
            state = await run_state()
            if state in TERMINAL_STATES and not rows:
                return

            if time.monotonic() - last_heartbeat >= 15:
                last_heartbeat = time.monotonic()
                yield _frame(
                    None,
                    "heartbeat",
                    {"at": datetime.now(UTC).isoformat(), "state": state or "unknown"},
                )

            await asyncio.sleep(0.15)
