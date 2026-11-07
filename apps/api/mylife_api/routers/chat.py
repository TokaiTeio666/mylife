"""chat 路由（API-CONTRACTS §3.6 / §6）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from mylife_contract.chat import (
    CancelResponse,
    ChatTurnRequest,
    ChatTurnResponse,
    ConversationList,
    ConversationSummary,
    CreateConversationRequest,
    MessageList,
    RunDetail,
    UpdateConversationRequest,
)
from mylife_contract.errors import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repo_chat as repo
from mylife_api import services_chat
from mylife_api.chat_runner import TERMINAL_STATES
from mylife_api.deps import RunContext, get_db, get_run_context
from mylife_api.errors import AppError
from mylife_api.runtime import STREAM_EPOCH

router = APIRouter(tags=["chat"])


@router.get("/conversations", response_model=ConversationList)
async def list_conversations(
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ConversationList:
    return await services_chat.list_conversations(
        db, workspace_id=ctx.workspace_id, user_id=ctx.user_id, q=q, limit=limit
    )


@router.post(
    "/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: CreateConversationRequest,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummary:
    return await services_chat.create_conversation(
        db, workspace_id=ctx.workspace_id, user_id=ctx.user_id, payload=payload
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationSummary)
async def get_conversation(
    conversation_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummary:
    return await services_chat.get_conversation_summary(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        conversation_id=conversation_id,
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: UUID,
    payload: UpdateConversationRequest,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummary:
    return await services_chat.rename_conversation(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        conversation_id=conversation_id,
        payload=payload,
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await services_chat.delete_conversation(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        conversation_id=conversation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/conversations/{conversation_id}/messages", response_model=MessageList)
async def list_messages(
    conversation_id: UUID,
    limit: int = Query(default=30, ge=1, le=100),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> MessageList:
    return await services_chat.list_messages(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        conversation_id=conversation_id,
        limit=limit,
    )


@router.post(
    "/conversations/{conversation_id}/turns",
    response_model=ChatTurnResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_turn(
    conversation_id: UUID,
    payload: ChatTurnRequest,
    response: Response,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> ChatTurnResponse:
    result, replayed = await services_chat.create_turn(
        db,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        conversation_id=conversation_id,
        payload=payload,
    )
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return result


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> RunDetail:
    return await services_chat.get_run_detail(
        db, workspace_id=ctx.workspace_id, run_id=run_id
    )


@router.post(
    "/runs/{run_id}/cancel",
    response_model=CancelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_run(
    run_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> CancelResponse:
    result = await services_chat.cancel_run(
        db, workspace_id=ctx.workspace_id, run_id=run_id
    )
    return CancelResponse(**result)


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: UUID,
    after: int = Query(default=0, ge=0),
    epoch: int = Query(default=0, ge=0),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    run = await repo.get_run(db, workspace_id=ctx.workspace_id, run_id=run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND)

    if after > 0:
        oldest = await repo.oldest_event_id(
            db, workspace_id=ctx.workspace_id, run_id=run_id
        )
        if (
            oldest is not None
            and after < oldest - 1
            and run["state"] in TERMINAL_STATES
        ):
            raise AppError(
                ErrorCode.EVENT_CURSOR_EXPIRED,
                details={"oldest_available_event_id": oldest},
            )

    generator = services_chat.stream_run_events(
        workspace_id=ctx.workspace_id, run_id=run_id, after=after, epoch=epoch
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "X-Stream-Epoch": str(STREAM_EPOCH),
        },
    )
