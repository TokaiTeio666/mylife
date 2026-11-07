"""Phase 2 数据访问：会话、消息、agent_runs、run_steps、run_events。"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api.ids import uuid7


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row._mapping) if row is not None else None


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


async def list_conversations(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    q: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT count(*) FROM messages m
                     WHERE m.conversation_id = c.id AND m.role IN ('user','assistant')
                       AND m.status <> 'drafting') AS message_count,
                   COALESCE((
                     SELECT left(m.content, 120) FROM messages m
                     WHERE m.conversation_id = c.id AND m.role IN ('user','assistant')
                       AND m.status <> 'drafting'
                     ORDER BY m.created_at DESC, m.id DESC LIMIT 1
                   ), '') AS last_message_preview
            FROM conversations c
            WHERE c.workspace_id = :ws AND c.user_id = :uid AND c.deleted_at IS NULL
              AND (CAST(:q AS text) IS NULL OR c.title ILIKE :q)
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT :limit
            """
        ),
        {
            "ws": workspace_id,
            "uid": user_id,
            "q": f"%{q}%" if q else None,
            "limit": limit,
        },
    )
    return _rows(result)


async def get_conversation_summary(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    conversation_id: UUID,
    user_id: UUID,
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT count(*) FROM messages m
                     WHERE m.conversation_id = c.id AND m.role IN ('user','assistant')
                       AND m.status <> 'drafting') AS message_count,
                   COALESCE((
                     SELECT left(m.content, 120) FROM messages m
                     WHERE m.conversation_id = c.id AND m.role IN ('user','assistant')
                       AND m.status <> 'drafting'
                     ORDER BY m.created_at DESC, m.id DESC LIMIT 1
                   ), '') AS last_message_preview
            FROM conversations c
            WHERE c.workspace_id = :ws AND c.id = :id AND c.user_id = :uid
              AND c.deleted_at IS NULL
            """
        ),
        {"ws": workspace_id, "id": conversation_id, "uid": user_id},
    )
    return _row(result.first())


async def get_conversation(
    session: AsyncSession, *, workspace_id: UUID, conversation_id: UUID, user_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, title, created_at, updated_at, deleted_at FROM conversations "
            "WHERE workspace_id = :ws AND id = :id AND user_id = :uid AND deleted_at IS NULL"
        ),
        {"ws": workspace_id, "id": conversation_id, "uid": user_id},
    )
    return _row(result.first())


async def insert_conversation(
    session: AsyncSession, *, workspace_id: UUID, user_id: UUID, title: str
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO conversations (id, workspace_id, user_id, title) "
            "VALUES (:id, :ws, :uid, :title) "
            "RETURNING id, title, created_at, updated_at"
        ),
        {"id": uuid7(), "ws": workspace_id, "uid": user_id, "title": title},
    )
    row = dict(result.one()._mapping)
    row["message_count"] = 0
    row["last_message_preview"] = ""
    return row


async def rename_conversation(
    session: AsyncSession, *, conversation_id: UUID, title: str
) -> None:
    await session.execute(
        text("UPDATE conversations SET title = :title, updated_at = now() WHERE id = :id"),
        {"title": title, "id": conversation_id},
    )


async def soft_delete_conversation(session: AsyncSession, conversation_id: UUID) -> None:
    await session.execute(
        text("UPDATE conversations SET deleted_at = now() WHERE id = :id"),
        {"id": conversation_id},
    )


async def touch_conversation(session: AsyncSession, conversation_id: UUID) -> None:
    await session.execute(
        text("UPDATE conversations SET updated_at = now() WHERE id = :id"),
        {"id": conversation_id},
    )


async def count_conversations(session: AsyncSession, workspace_id: UUID) -> int:
    result = await session.execute(
        text(
            "SELECT count(*) FROM conversations "
            "WHERE workspace_id = :ws AND deleted_at IS NULL"
        ),
        {"ws": workspace_id},
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------


async def list_messages(
    session: AsyncSession, *, conversation_id: UUID, limit: int
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, conversation_id, role, content, status, references_json, "
            "       tool_events_json, usage_json, run_id, created_at "
            "FROM messages WHERE conversation_id = :cid AND role <> 'system' "
            "ORDER BY created_at ASC, id ASC LIMIT :limit"
        ),
        {"cid": conversation_id, "limit": limit},
    )
    return _rows(result)


async def insert_message(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    conversation_id: UUID,
    role: str,
    content: str,
    status: str,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO messages "
            "(id, workspace_id, conversation_id, run_id, role, content, status) "
            "VALUES (:id, :ws, :cid, :run, :role, :content, :status) "
            "RETURNING id, conversation_id, role, content, status, run_id, created_at"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "cid": conversation_id,
            "run": run_id,
            "role": role,
            "content": content,
            "status": status,
        },
    )
    return dict(result.one()._mapping)


async def finalize_assistant_message(
    session: AsyncSession,
    *,
    message_id: UUID,
    content: str,
    status: str,
    references: list[dict[str, Any]],
    tool_events: list[dict[str, Any]],
    usage: dict[str, Any] | None,
) -> None:
    await session.execute(
        text(
            "UPDATE messages SET content = :content, status = :status, "
            "references_json = CAST(:refs AS jsonb), "
            "tool_events_json = CAST(:tools AS jsonb), "
            "usage_json = CAST(:usage AS jsonb) WHERE id = :id"
        ),
        {
            "content": content,
            "status": status,
            "refs": json.dumps(references, ensure_ascii=False),
            "tools": json.dumps(tool_events, ensure_ascii=False),
            "usage": json.dumps(usage, ensure_ascii=False) if usage else None,
            "id": message_id,
        },
    )


# ---------------------------------------------------------------------------
# runs / steps / events
# ---------------------------------------------------------------------------


async def find_turn_run(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    conversation_id: UUID,
    client_turn_id: UUID,
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, state, conversation_id, client_turn_id, state_payload "
            "FROM agent_runs WHERE workspace_id = :ws AND conversation_id = :cid "
            "  AND client_turn_id = :turn AND kind = 'chat'"
        ),
        {"ws": workspace_id, "cid": conversation_id, "turn": client_turn_id},
    )
    return _row(result.first())


async def insert_chat_run(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    conversation_id: UUID,
    client_turn_id: UUID,
    state_payload: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    run_id = uuid7()
    attempt_id = uuid7()
    result = await session.execute(
        text(
            "INSERT INTO agent_runs "
            "(id, workspace_id, kind, conversation_id, client_turn_id, state, "
            " state_payload, attempt_id, idem_key, trace_id) "
            "VALUES (:id, :ws, 'chat', :cid, :turn, 'created', "
            "        CAST(:payload AS jsonb), :attempt, :idem, :trace) "
            "RETURNING id, state, conversation_id, client_turn_id, attempt_id, created_at"
        ),
        {
            "id": run_id,
            "ws": workspace_id,
            "cid": conversation_id,
            "turn": client_turn_id,
            "payload": json.dumps(state_payload, ensure_ascii=False),
            "attempt": attempt_id,
            "idem": f"chat:{conversation_id}:{client_turn_id}",
            "trace": trace_id,
        },
    )
    return dict(result.one()._mapping)


async def get_run(
    session: AsyncSession, *, workspace_id: UUID, run_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, workspace_id, kind, conversation_id, client_turn_id, state, "
            "       state_payload, attempt_id, retry_from_state, snapshot_id, "
            "       review_id, version, lease_expires_at, created_at, updated_at, "
            "       finished_at FROM agent_runs "
            "WHERE workspace_id = :ws AND id = :id"
        ),
        {"ws": workspace_id, "id": run_id},
    )
    return _row(result.first())


async def list_active_conversation_runs(
    session: AsyncSession, *, workspace_id: UUID, conversation_id: UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, state FROM agent_runs WHERE workspace_id = :ws "
            "AND conversation_id = :cid AND kind = 'chat' "
            "AND state IN ('created','retrieving','drafting','verifying')"
        ),
        {"ws": workspace_id, "cid": conversation_id},
    )
    return _rows(result)


async def update_run_state(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    state: str,
    state_payload: dict[str, Any] | None = None,
    finished: bool = False,
) -> None:
    if state_payload is None:
        await session.execute(
            text(
                "UPDATE agent_runs SET state = :state, updated_at = now(), "
                "finished_at = CASE WHEN :finished THEN now() ELSE finished_at END "
                "WHERE workspace_id = :ws AND id = :id"
            ),
            {"state": state, "finished": finished, "ws": workspace_id, "id": run_id},
        )
        return
    await session.execute(
        text(
            "UPDATE agent_runs SET state = :state, state_payload = CAST(:payload AS jsonb), "
            "updated_at = now(), "
            "finished_at = CASE WHEN :finished THEN now() ELSE finished_at END "
            "WHERE workspace_id = :ws AND id = :id"
        ),
        {
            "state": state,
            "payload": json.dumps(state_payload, ensure_ascii=False),
            "finished": finished,
            "ws": workspace_id,
            "id": run_id,
        },
    )


async def insert_event(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    event: str,
    payload: dict[str, Any],
    from_state: str | None = None,
    to_state: str | None = None,
) -> int:
    result = await session.execute(
        text(
            "INSERT INTO run_events "
            "(workspace_id, run_id, from_state, to_state, event, payload) "
            "VALUES (:ws, :run, :from_state, :to_state, :event, CAST(:payload AS jsonb)) "
            "RETURNING event_id"
        ),
        {
            "ws": workspace_id,
            "run": run_id,
            "from_state": from_state,
            "to_state": to_state,
            "event": event,
            "payload": json.dumps(payload, ensure_ascii=False, default=str),
        },
    )
    return int(result.scalar_one())


async def list_events(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    after: int,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT event_id, event, payload, from_state, to_state, created_at "
            "FROM run_events WHERE workspace_id = :ws AND run_id = :run "
            "AND event_id > :after ORDER BY event_id ASC LIMIT :limit"
        ),
        {"ws": workspace_id, "run": run_id, "after": after, "limit": limit},
    )
    return _rows(result)


async def oldest_event_id(
    session: AsyncSession, *, workspace_id: UUID, run_id: UUID
) -> int | None:
    result = await session.execute(
        text(
            "SELECT min(event_id) FROM run_events WHERE workspace_id = :ws AND run_id = :run"
        ),
        {"ws": workspace_id, "run": run_id},
    )
    return result.scalar_one_or_none()


async def next_step_seq(session: AsyncSession, run_id: UUID) -> int:
    result = await session.execute(
        text("SELECT COALESCE(max(seq), 0) + 1 FROM run_steps WHERE run_id = :run"),
        {"run": run_id},
    )
    return int(result.scalar_one())


async def insert_step(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    seq: int,
    role: str,
    tool_name: str | None,
    status: str,
    input_digest: str | None,
    output_digest: str | None,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO run_steps "
            "(id, workspace_id, run_id, seq, role, tool_name, status, input_digest, "
            " output_digest, model, tokens_in, tokens_out, latency_ms) "
            "VALUES (:id, :ws, :run, :seq, :role, :tool, :status, :in_digest, "
            "        :out_digest, :model, :tokens_in, :tokens_out, :latency) "
            "ON CONFLICT (run_id, seq) DO NOTHING"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "run": run_id,
            "seq": seq,
            "role": role,
            "tool": tool_name,
            "status": status,
            "in_digest": input_digest,
            "out_digest": output_digest,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "latency": latency_ms,
        },
    )


async def list_steps(session: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT seq, role, tool_name, status, model, "
            "       (tokens_in + tokens_out) AS tokens, latency_ms "
            "FROM run_steps WHERE run_id = :run ORDER BY seq ASC"
        ),
        {"run": run_id},
    )
    return _rows(result)


async def insert_usage(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    degraded: bool = False,
    run_id: UUID | None = None,
    attempt_id: UUID | None = None,
    kind: str = "chat",
    role: str | None = None,
    cost: float = 0.0,
) -> None:
    await session.execute(
        text(
            "INSERT INTO usage_events "
            "(id, workspace_id, user_id, kind, model, prompt_tokens, completion_tokens, "
            " latency_ms, degraded, run_id, attempt_id, role, cost) "
            "VALUES (:id, :ws, :uid, :kind, :model, :prompt, :completion, "
            "        :latency, :degraded, :run, :attempt, :role, :cost)"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "uid": user_id,
            "kind": kind,
            "model": model,
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "latency": latency_ms,
            "degraded": degraded,
            "run": run_id,
            "attempt": attempt_id,
            "role": role,
            "cost": cost,
        },
    )


# ---------------------------------------------------------------------------
# 工具数据
# ---------------------------------------------------------------------------


async def list_tasks_for_tool(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    statuses: list[str] | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
) -> list[dict[str, Any]]:
    conditions = ["workspace_id = :ws"]
    params: dict[str, Any] = {"ws": workspace_id, "limit": limit}
    if statuses:
        conditions.append("status = ANY(:statuses)")
        params["statuses"] = statuses
    if date_from:
        conditions.append("last_seen >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("first_seen <= :date_to")
        params["date_to"] = date_to
    result = await session.execute(
        text(
            "SELECT id, text, status, first_seen, last_seen, done_date, source_path, "
            "       line_number FROM tasks "
            f"WHERE {' AND '.join(conditions)} ORDER BY first_seen ASC, id ASC LIMIT :limit"
        ),
        params,
    )
    return _rows(result)


async def task_status_counts(session: AsyncSession, workspace_id: UUID) -> dict[str, int]:
    result = await session.execute(
        text(
            "SELECT status, count(*) AS n FROM tasks WHERE workspace_id = :ws GROUP BY status"
        ),
        {"ws": workspace_id},
    )
    return {row.status: int(row.n) for row in result}


async def task_ledger_recalc(
    session: AsyncSession, workspace_id: UUID
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "SELECT count(*) AS total, "
            "       count(*) FILTER (WHERE status = 'done') AS completed "
            "FROM tasks WHERE workspace_id = :ws"
        ),
        {"ws": workspace_id},
    )
    row = result.one()
    total = int(row.total)
    completed = int(row.completed)
    return {
        "completed": completed,
        "total": total,
        "rate": round(completed / total, 4) if total else 0.0,
    }


async def get_review_for_tool(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID | None,
    kind: str | None,
    period_key: str | None,
) -> dict[str, Any] | None:
    if review_id is not None:
        result = await session.execute(
            text(
                "SELECT id, kind, period_key, title, version, content, generated_at "
                "FROM reviews WHERE workspace_id = :ws AND id = :id"
            ),
            {"ws": workspace_id, "id": review_id},
        )
    else:
        result = await session.execute(
            text(
                "SELECT id, kind, period_key, title, version, content, generated_at "
                "FROM reviews WHERE workspace_id = :ws AND kind = :kind "
                "AND period_key = :period ORDER BY version DESC LIMIT 1"
            ),
            {"ws": workspace_id, "kind": kind, "period": period_key},
        )
    return _row(result.first())


async def metrics_snapshot(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    from_ts: Any | None = None,
    to_ts: Any | None = None,
) -> dict[str, Any]:
    conditions = ["workspace_id = :ws"]
    params: dict[str, Any] = {"ws": workspace_id}
    if from_ts is not None:
        conditions.append("created_at >= :from_ts")
        params["from_ts"] = from_ts
    if to_ts is not None:
        conditions.append("created_at < :to_ts")
        params["to_ts"] = to_ts
    usage = await session.execute(
        text(
            "SELECT COALESCE(sum(prompt_tokens),0) AS prompt, "
            "       COALESCE(sum(completion_tokens),0) AS completion, "
            "       count(*) AS calls, "
            "       count(*) FILTER (WHERE degraded) AS degraded "
            "FROM usage_events WHERE " + " AND ".join(conditions)
        ),
        params,
    )
    row = usage.one()
    reviews = await session.execute(
        text("SELECT count(*) FROM reviews WHERE workspace_id = :ws"), {"ws": workspace_id}
    )
    return {
        "prompt_tokens": int(row.prompt),
        "completion_tokens": int(row.completion),
        "calls": int(row.calls),
        "degraded_calls": int(row.degraded),
        "review_count": int(reviews.scalar_one()),
    }


async def latest_snapshot_id(session: AsyncSession, workspace_id: UUID) -> UUID | None:
    result = await session.execute(
        text(
            "SELECT id FROM source_snapshots WHERE workspace_id = :ws "
            "ORDER BY as_of DESC LIMIT 1"
        ),
        {"ws": workspace_id},
    )
    return result.scalar_one_or_none()


async def max_event_id(
    session: AsyncSession, *, workspace_id: UUID, run_id: UUID
) -> int:
    result = await session.execute(
        text(
            "SELECT COALESCE(max(event_id), 0) FROM run_events "
            "WHERE workspace_id = :ws AND run_id = :run"
        ),
        {"ws": workspace_id, "run": run_id},
    )
    return int(result.scalar_one())


async def update_message_status(session: AsyncSession, message_id: UUID, status: str) -> None:
    await session.execute(
        text("UPDATE messages SET status = :status WHERE id = :id"),
        {"status": status, "id": message_id},
    )


async def get_chunk_contents(
    session: AsyncSession, chunk_ids: list[UUID]
) -> dict[UUID, dict[str, Any]]:
    if not chunk_ids:
        return {}
    result = await session.execute(
        text(
            "SELECT id, document_id, heading, content, line_start, line_end "
            "FROM document_chunks WHERE id = ANY(:ids)"
        ),
        {"ids": chunk_ids},
    )
    return {row._mapping["id"]: dict(row._mapping) for row in result}


async def evidence_snapshot_for_manifest(
    session: AsyncSession, *, workspace_id: UUID, document_id: UUID
) -> UUID | None:
    result = await session.execute(
        text(
            "SELECT snapshot_id FROM snapshot_documents "
            "WHERE workspace_id = :ws AND document_id = :doc "
            "ORDER BY snapshot_id DESC LIMIT 1"
        ),
        {"ws": workspace_id, "doc": document_id},
    )
    return result.scalar_one_or_none()
