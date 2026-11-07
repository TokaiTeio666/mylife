"""复盘数据访问（API-CONTRACTS §3.7；ARCHITECTURE §6 版本隔离）。

所有查询都带 `workspace_id`；版本分区一律以 `(review_id, version)` 限定。
"""

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
# 准入
# ---------------------------------------------------------------------------


async def lock_period(
    session: AsyncSession, *, workspace_id: UUID, kind: str, period_key: str
) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"review:{workspace_id}:{kind}:{period_key}"},
    )


async def find_active_review_run(
    session: AsyncSession, *, workspace_id: UUID, kind: str, period_key: str
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, state, created_at FROM agent_runs "
            "WHERE workspace_id = :ws AND kind = :kind AND period_key = :period "
            "  AND is_active_state(state) ORDER BY created_at ASC LIMIT 1"
        ),
        {"ws": workspace_id, "kind": kind, "period": period_key},
    )
    return _row(result.first())


async def count_active_review_runs(session: AsyncSession, *, workspace_id: UUID) -> int:
    result = await session.execute(
        text(
            "SELECT count(*) FROM agent_runs WHERE workspace_id = :ws "
            "AND kind IN ('daily','weekly','yearly') AND is_active_state(state)"
        ),
        {"ws": workspace_id},
    )
    return int(result.scalar_one())


async def get_review_by_period(
    session: AsyncSession, *, workspace_id: UUID, kind: str, period_key: str
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, kind, period_key, title, version, content, status, git_path, "
            "       content_hash, snapshot_id, degraded, degraded_reasons, verdict_json, "
            "       model, generated_at FROM reviews "
            "WHERE workspace_id = :ws AND kind = :kind AND period_key = :period"
        ),
        {"ws": workspace_id, "kind": kind, "period": period_key},
    )
    return _row(result.first())


async def get_review(
    session: AsyncSession, *, workspace_id: UUID, review_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, kind, period_key, title, version, content, status, git_path, "
            "       content_hash, snapshot_id, degraded, degraded_reasons, verdict_json, "
            "       model, generated_at FROM reviews "
            "WHERE workspace_id = :ws AND id = :id"
        ),
        {"ws": workspace_id, "id": review_id},
    )
    return _row(result.first())


async def insert_review(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    kind: str,
    period_key: str,
    title: str,
    version: int,
    snapshot_id: UUID | None,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO reviews (id, workspace_id, kind, period_key, title, content, "
            "  version, status, snapshot_id) "
            "VALUES (:id, :ws, :kind, :period, :title, '', :version, 'generating', :snap) "
            "RETURNING id, version"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "kind": kind,
            "period": period_key,
            "title": title,
            "version": version,
            "snap": snapshot_id,
        },
    )
    return dict(result.one()._mapping)


async def next_review_version(session: AsyncSession, *, workspace_id: UUID, review_id: UUID) -> int:
    result = await session.execute(
        text(
            "SELECT COALESCE(max(version), 0) + 1 FROM review_versions "
            "WHERE workspace_id = :ws AND review_id = :id"
        ),
        {"ws": workspace_id, "id": review_id},
    )
    return int(result.scalar_one())


async def insert_review_run(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    kind: str,
    period_key: str,
    review_id: UUID,
    version: int,
    state_payload: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    run_id = uuid7()
    result = await session.execute(
        text(
            "INSERT INTO agent_runs (id, workspace_id, kind, period_key, state, "
            "  state_payload, attempt_id, idem_key, trace_id, review_id, version) "
            "VALUES (:id, :ws, :kind, :period, 'created', CAST(:payload AS jsonb), "
            "        :attempt, :idem, :trace, :review, :version) "
            "RETURNING id, state, created_at"
        ),
        {
            "id": run_id,
            "ws": workspace_id,
            "kind": kind,
            "period": period_key,
            "payload": json.dumps(state_payload, ensure_ascii=False, default=str),
            "attempt": uuid7(),
            "idem": f"review:{kind}:{period_key}:{version}",
            "trace": trace_id,
            "review": review_id,
            "version": version,
        },
    )
    return dict(result.one()._mapping)


# ---------------------------------------------------------------------------
# run / 状态
# ---------------------------------------------------------------------------


async def get_run(
    session: AsyncSession, *, workspace_id: UUID, run_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, kind, period_key, state, state_payload, attempt_id, "
            "       retry_from_state, snapshot_id, review_id, version, lease_expires_at, "
            "       created_at, updated_at, finished_at FROM agent_runs "
            "WHERE workspace_id = :ws AND id = :id"
        ),
        {"ws": workspace_id, "id": run_id},
    )
    return _row(result.first())


async def cas_transition(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    from_state: str,
    to_state: str,
    reason: str,
    attempt_id: UUID | None = None,
    lease_owner: str | None = None,
    state_payload: dict[str, Any] | None = None,
    stream_epoch: int = 0,
) -> bool:
    """CAS 状态迁移 + 同事务写 run.state 事件；返回是否成功。"""
    terminal = to_state in {
        "succeeded",
        "failed",
        "cancelled",
        "needs_human",
        "security_abort",
    }
    params: dict[str, Any] = {
        "ws": workspace_id,
        "id": run_id,
        "from": from_state,
        "to": to_state,
        "finished": terminal,
        "payload": json.dumps(state_payload, ensure_ascii=False, default=str)
        if state_payload is not None
        else None,
        "attempt": attempt_id,
        "owner": lease_owner,
    }
    conditions = "workspace_id = :ws AND id = :id AND state = :from"
    if lease_owner is not None:
        # 准入时尚未写租约：允许首次认领，之后必须与持有者一致。
        conditions += " AND (lease_owner IS NULL OR lease_owner = :owner)"
        params["owner"] = lease_owner

    result = await session.execute(
        text(
            "UPDATE agent_runs SET state = :to, updated_at = now(), "
            "  lease_owner = COALESCE(:owner, lease_owner), "
            "  lease_expires_at = CASE WHEN :to = 'human_gate' THEN NULL "
            "    ELSE now() + interval '90 seconds' END, "
            "  finished_at = CASE WHEN :finished THEN now() ELSE finished_at END, "
            "  attempt_id = COALESCE(:attempt, attempt_id), "
            "  state_payload = COALESCE(CAST(:payload AS jsonb), state_payload) "
            f"WHERE {conditions} RETURNING id"
        ),
        params,
    )
    if result.first() is None:
        return False

    await session.execute(
        text(
            "INSERT INTO run_events "
            "(workspace_id, run_id, from_state, to_state, event, payload, stream_epoch) "
            "VALUES (:ws, :id, :from, :to, 'run.state', CAST(:payload AS jsonb), :epoch)"
        ),
        {
            "ws": workspace_id,
            "id": run_id,
            "from": from_state,
            "to": to_state,
            "payload": json.dumps(
                {
                    "event": "state.transition",
                    "from_state": from_state,
                    "to_state": to_state,
                    "attempt_id": str(attempt_id) if attempt_id else None,
                    "reason": reason,
                },
                ensure_ascii=False,
            ),
            "epoch": stream_epoch,
        },
    )
    return True


async def list_run_steps(session: AsyncSession, *, run_id: UUID) -> list[dict[str, Any]]:
    from mylife_api import repo_chat

    return await repo_chat.list_steps(session, run_id)


async def renew_lease(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    lease_owner: str,
    state: str,
) -> None:
    await session.execute(
        text(
            "UPDATE agent_runs SET lease_expires_at = now() + interval '90 seconds' "
            "WHERE workspace_id = :ws AND id = :id AND lease_owner = :owner AND state = :state"
        ),
        {"ws": workspace_id, "id": run_id, "owner": lease_owner, "state": state},
    )


# ---------------------------------------------------------------------------
# sections / entries / citations / versions
# ---------------------------------------------------------------------------


async def insert_sections(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID,
    version: int,
    sections: list[dict[str, Any]],
) -> None:
    if not sections:
        return
    await session.execute(
        text(
            "INSERT INTO review_sections "
            "(id, workspace_id, review_id, version, section_key, ordinal, content_md, "
            " structured_json) "
            "VALUES (:id, :ws, :review, :version, :key, :ordinal, :content, "
            "        CAST(:structured AS jsonb)) "
            "ON CONFLICT (review_id, version, section_key) DO UPDATE SET "
            "  content_md = EXCLUDED.content_md, structured_json = EXCLUDED.structured_json"
        ),
        [
            {
                "id": uuid7(),
                "ws": workspace_id,
                "review": review_id,
                "version": version,
                "key": section["section_key"],
                "ordinal": section["ordinal"],
                "content": section["content_md"],
                "structured": json.dumps(
                    section.get("structured") or {}, ensure_ascii=False, default=str
                ),
            }
            for section in sections
        ],
    )


async def list_sections(
    session: AsyncSession, *, workspace_id: UUID, review_id: UUID, version: int
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT section_key, ordinal, content_md, structured_json FROM review_sections "
            "WHERE workspace_id = :ws AND review_id = :review AND version = :version "
            "ORDER BY ordinal ASC"
        ),
        {"ws": workspace_id, "review": review_id, "version": version},
    )
    return _rows(result)


async def insert_entries(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID,
    version: int,
    entries: list[Any],
) -> None:
    if not entries:
        return
    await session.execute(
        text(
            "INSERT INTO review_entries "
            "(id, workspace_id, review_id, version, section_key, item_type, canonical_key, "
            " fields_json, source_path, line_start, line_end, evidence_id, source_date) "
            "VALUES (:id, :ws, :review, :version, :section, :type, :key, "
            "        CAST(:fields AS jsonb), :path, :line_start, :line_end, :evidence, :date) "
            "ON CONFLICT (review_id, version, section_key, item_type, canonical_key) DO NOTHING"
        ),
        [
            {
                "id": uuid7(),
                "ws": workspace_id,
                "review": review_id,
                "version": version,
                "section": entry.section_key,
                "type": entry.item_type,
                "key": entry.canonical_key,
                "fields": json.dumps(entry.fields, ensure_ascii=False, default=str),
                "path": entry.source_path,
                "line_start": entry.line_range[0] if entry.line_range else None,
                "line_end": entry.line_range[1] if entry.line_range else None,
                "evidence": entry.evidence_id,
                "date": entry.source_date,
            }
            for entry in entries
        ],
    )


async def list_entries(
    session: AsyncSession, *, workspace_id: UUID, review_id: UUID, version: int
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT section_key, item_type, canonical_key, fields_json, source_path, "
            "       line_start, line_end, confidence FROM review_entries "
            "WHERE workspace_id = :ws AND review_id = :review AND version = :version "
            "ORDER BY section_key, canonical_key"
        ),
        {"ws": workspace_id, "review": review_id, "version": version},
    )
    return _rows(result)


async def insert_citations(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID,
    version: int,
    citations: list[dict[str, Any]],
) -> None:
    if not citations:
        return
    await session.execute(
        text(
            "INSERT INTO review_citations "
            "(id, workspace_id, review_id, version, claim_id, evidence_id, source_path, "
            " source_date, document_content_hash, line_start, line_end, quote, quote_hash) "
            "VALUES (:id, :ws, :review, :version, :claim, :evidence, :path, :date, :hash, "
            "        :line_start, :line_end, :quote, :quote_hash) "
            "ON CONFLICT (review_id, version, claim_id, evidence_id) DO NOTHING"
        ),
        [
            {
                "id": uuid7(),
                "ws": workspace_id,
                "review": review_id,
                "version": version,
                "claim": item["claim_id"],
                "evidence": item["evidence_id"],
                "path": item.get("source_path"),
                "date": item.get("source_date"),
                "hash": item.get("document_content_hash"),
                "line_start": (item.get("line_range") or [None, None])[0],
                "line_end": (item.get("line_range") or [None, None])[1],
                "quote": item["quote"],
                "quote_hash": item["quote_hash"],
            }
            for item in citations
        ],
    )


async def list_citations(
    session: AsyncSession, *, workspace_id: UUID, review_id: UUID, version: int
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT claim_id, evidence_id, source_path, source_date, line_start, line_end, quote "
            "FROM review_citations WHERE workspace_id = :ws AND review_id = :review "
            "AND version = :version ORDER BY claim_id, evidence_id"
        ),
        {"ws": workspace_id, "review": review_id, "version": version},
    )
    return _rows(result)


async def insert_version(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID,
    version: int,
    content_md: str,
    content_hash: str,
    model: str,
    prompt_version: str,
    snapshot_id: UUID | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO review_versions "
            "(id, workspace_id, review_id, version, content_md, content_hash, model, "
            " prompt_version, snapshot_id) "
            "VALUES (:id, :ws, :review, :version, :content, :hash, :model, :prompt, :snap) "
            "ON CONFLICT (review_id, version) DO UPDATE SET "
            "  content_md = EXCLUDED.content_md, content_hash = EXCLUDED.content_hash"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "review": review_id,
            "version": version,
            "content": content_md,
            "hash": content_hash,
            "model": model,
            "prompt": prompt_version,
            "snap": snapshot_id,
        },
    )


async def list_versions(
    session: AsyncSession, *, workspace_id: UUID, review_id: UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT version, content_hash, git_sha, created_at FROM review_versions "
            "WHERE workspace_id = :ws AND review_id = :review ORDER BY version DESC"
        ),
        {"ws": workspace_id, "review": review_id},
    )
    return _rows(result)


async def get_version(
    session: AsyncSession, *, workspace_id: UUID, review_id: UUID, version: int
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT version, content_md, content_hash, model, snapshot_id, created_at "
            "FROM review_versions WHERE workspace_id = :ws AND review_id = :review "
            "AND version = :version"
        ),
        {"ws": workspace_id, "review": review_id, "version": version},
    )
    return _row(result.first())


async def finalize_review(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID,
    version: int,
    content_md: str,
    content_hash: str,
    model: str,
    git_path: str,
    status: str,
    verdict: dict[str, Any] | None,
    degraded: bool = False,
    degraded_reasons: list[str] | None = None,
) -> None:
    await session.execute(
        text(
            "UPDATE reviews SET content = :content, content_hash = :hash, model = :model, "
            "  git_path = :path, status = :status, version = :version, "
            "  verdict_json = CAST(:verdict AS jsonb), degraded = :degraded, "
            "  degraded_reasons = :reasons, generated_at = now(), updated_at = now() "
            "WHERE workspace_id = :ws AND id = :id"
        ),
        {
            "content": content_md,
            "hash": content_hash,
            "model": model,
            "path": git_path,
            "status": status,
            "version": version,
            "verdict": json.dumps(verdict, ensure_ascii=False, default=str) if verdict else None,
            "degraded": degraded,
            "reasons": degraded_reasons or [],
            "ws": workspace_id,
            "id": review_id,
        },
    )


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


async def list_reviews(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    kind: str | None,
    status: str | None,
    limit: int,
    after: tuple[str, int] | None = None,
) -> list[dict[str, Any]]:
    conditions = ["workspace_id = :ws"]
    params: dict[str, Any] = {"ws": workspace_id, "limit": limit}
    if kind:
        conditions.append("kind = :kind")
        params["kind"] = kind
    if status:
        conditions.append("status = :status")
        params["status"] = status
    if after:
        conditions.append("(period_key, version) < (:after_period, :after_version)")
        params["after_period"] = after[0]
        params["after_version"] = after[1]
    result = await session.execute(
        text(
            "SELECT id, kind, period_key, title, version, status, git_path, degraded, "
            "       generated_at FROM reviews WHERE "
            + " AND ".join(conditions)
            + " ORDER BY period_key DESC, version DESC LIMIT :limit"
        ),
        params,
    )
    return _rows(result)


# ---------------------------------------------------------------------------
# evidence / topics
# ---------------------------------------------------------------------------


async def insert_evidence(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    snapshot_id: UUID,
    items: list[dict[str, Any]],
) -> None:
    if not items:
        return
    await session.execute(
        text(
            "INSERT INTO evidence_items "
            "(id, workspace_id, run_id, evidence_id, snapshot_id, source_type, document_id, "
            " chunk_id, source_path, source_date, occurred_on, document_content_hash, heading, "
            " tags, line_start, line_end, quote, quote_hash) "
            "VALUES (:id, :ws, :run, :evidence, :snap, :type, :doc, :chunk, :path, :date, "
            "        :occurred, :hash, :heading, :tags, :line_start, :line_end, :quote, "
            "        :quote_hash) "
            "ON CONFLICT (run_id, evidence_id) DO NOTHING"
        ),
        [
            {
                "id": uuid7(),
                "ws": workspace_id,
                "run": run_id,
                "evidence": item["evidence_id"],
                "snap": snapshot_id,
                "type": item["source_type"],
                "doc": item.get("document_id"),
                "chunk": item.get("chunk_id"),
                "path": item.get("source_path"),
                "date": item.get("source_date"),
                "occurred": item.get("occurred_on"),
                "hash": item.get("document_content_hash"),
                "heading": item.get("heading"),
                "tags": item.get("tags") or [],
                "line_start": (item.get("line_range") or [None, None])[0],
                "line_end": (item.get("line_range") or [None, None])[1],
                "quote": item["quote"],
                "quote_hash": item["quote_hash"],
            }
            for item in items
        ],
    )


async def snapshot_hashes(
    session: AsyncSession, *, workspace_id: UUID, snapshot_id: UUID
) -> dict[str, str]:
    result = await session.execute(
        text(
            "SELECT path, content_hash FROM snapshot_documents "
            "WHERE workspace_id = :ws AND snapshot_id = :snap"
        ),
        {"ws": workspace_id, "snap": snapshot_id},
    )
    return {row.path: row.content_hash for row in result}


async def latest_snapshot(session: AsyncSession, *, workspace_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, storage_path, doc_count, as_of FROM source_snapshots "
            "WHERE workspace_id = :ws ORDER BY as_of DESC LIMIT 1"
        ),
        {"ws": workspace_id},
    )
    return _row(result.first())


async def upsert_topic_item(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    topic: str,
    canonical_key: str,
    title: str,
    source_date: date,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO topic_items "
            "(id, workspace_id, topic, canonical_key, title, first_seen, last_seen, "
            " occurrence_count, status) "
            "VALUES (:id, :ws, :topic, :key, :title, :date, :date, 1, 'pending') "
            "ON CONFLICT (workspace_id, topic, canonical_key) DO UPDATE SET "
            "  last_seen = GREATEST(topic_items.last_seen, EXCLUDED.last_seen), "
            "  occurrence_count = topic_items.occurrence_count + 1 "
            "RETURNING id, status, occurrence_count"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "topic": topic,
            "key": canonical_key,
            "title": title,
            "date": source_date,
        },
    )
    return dict(result.one()._mapping)


async def insert_topic_occurrence(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    item_id: UUID,
    source_date: date,
    source_path: str,
    line_range: list[int] | None,
    quote: str,
    evidence_id: str | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO topic_occurrences "
            "(id, workspace_id, item_id, source_date, source_path, line_start, line_end, "
            " quote, evidence_id) "
            "VALUES (:id, :ws, :item, :date, :path, :start, :end, :quote, :evidence) "
            "ON CONFLICT (item_id, source_date, source_path, line_start) DO NOTHING"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "item": item_id,
            "date": source_date,
            "path": source_path,
            "start": (line_range or [None, None])[0],
            "end": (line_range or [None, None])[1],
            "quote": quote,
            "evidence": evidence_id,
        },
    )


async def get_topic_item(
    session: AsyncSession, *, workspace_id: UUID, item_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, topic, canonical_key, title, first_seen, last_seen, "
            "       occurrence_count, status FROM topic_items "
            "WHERE workspace_id = :ws AND id = :id"
        ),
        {"ws": workspace_id, "id": item_id},
    )
    return _row(result.first())


async def list_topic_items(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    topic: str,
    status: str | None,
    include_pending: bool = False,
) -> list[dict[str, Any]]:
    conditions = ["workspace_id = :ws", "topic = :topic"]
    params: dict[str, Any] = {"ws": workspace_id, "topic": topic}
    if status:
        conditions.append("status = :status")
        params["status"] = status
    elif not include_pending:
        conditions.append("status <> 'pending'")
    result = await session.execute(
        text(
            "SELECT id, topic, canonical_key, title, first_seen, last_seen, "
            "       occurrence_count, status FROM topic_items WHERE "
            + " AND ".join(conditions)
            + " ORDER BY (status = 'pending') DESC, last_seen DESC"
        ),
        params,
    )
    return _rows(result)


async def list_topic_occurrences(
    session: AsyncSession, *, workspace_id: UUID, item_id: UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT source_date, source_path, line_start, line_end, quote, evidence_id "
            "FROM topic_occurrences WHERE workspace_id = :ws AND item_id = :item "
            "ORDER BY source_date ASC, source_path ASC"
        ),
        {"ws": workspace_id, "item": item_id},
    )
    return _rows(result)


async def update_topic_status(
    session: AsyncSession, *, workspace_id: UUID, item_id: UUID, status: str
) -> None:
    await session.execute(
        text("UPDATE topic_items SET status = :status WHERE workspace_id = :ws AND id = :id"),
        {"status": status, "ws": workspace_id, "id": item_id},
    )
