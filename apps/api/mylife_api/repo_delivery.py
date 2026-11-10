"""交付数据访问（API-CONTRACTS §3.7.10–§3.7.12；ARCHITECTURE §8）。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api.ids import uuid7

DELIVERY_SELECT = (
    "SELECT id, review_id, version, target, provider, branch, base_branch, commit_sha, "
    "       pr_number, pr_url, status, merged_commit, merged_at, auto_merge, error_code, "
    "       degraded, content_hash, manifest_json, diff_summary_json, created_at, updated_at "
    "FROM delivery_records "
)


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row._mapping) if row is not None else None


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in rows]


async def get_connection(
    session: AsyncSession, *, workspace_id: UUID, role: str
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, role, provider, repo_full_name, base_branch, auth_type, "
            "       credential_ciphertext, credential_key_version, reviews_root, mode, "
            "       sync_enabled, sync_interval_minutes, next_sync_at, last_sync_at, "
            "       last_sync_status, credential_status, last_tested_at, last_test_error, "
            "       remote_url, api_base_url "
            "FROM git_connections WHERE workspace_id = :ws AND role = :role "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"ws": workspace_id, "role": role},
    )
    return _row(result.first())


async def list_connections(
    session: AsyncSession, *, workspace_id: UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, role, provider, repo_full_name, base_branch, auth_type, reviews_root, "
            "       mode, sync_enabled, sync_interval_minutes, next_sync_at, last_sync_at, "
            "       last_sync_status, credential_status, last_tested_at, "
            "       (credential_ciphertext IS NOT NULL) AS has_credential "
            "FROM git_connections WHERE workspace_id = :ws ORDER BY role, created_at ASC"
        ),
        {"ws": workspace_id},
    )
    return _rows(result)


async def get_delivery(
    session: AsyncSession, *, workspace_id: UUID, delivery_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(DELIVERY_SELECT + "WHERE workspace_id = :ws AND id = :id"),
        {"ws": workspace_id, "id": delivery_id},
    )
    return _row(result.first())


async def list_deliveries(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID | None,
    status: str | None,
    limit: int,
    after: tuple[datetime, UUID] | None = None,
) -> list[dict[str, Any]]:
    conditions = ["workspace_id = :ws"]
    params: dict[str, Any] = {"ws": workspace_id, "limit": limit}
    if after is not None:
        conditions.append("(created_at, id) < (:after_ts, :after_id)")
        params["after_ts"] = after[0]
        params["after_id"] = after[1]
    if review_id is not None:
        conditions.append("review_id = :review")
        params["review"] = review_id
    if status:
        # 过滤参数是 API 枚举；DB 存储的是并集（conflict/committing/...）。
        api_to_db = {
            "pending": ["pending", "committing", "committed"],
            "blocked": ["blocked", "conflict", "failed"],
            "pr_open": ["pr_open"],
            "merged": ["merged"],
            "committed": ["committed"],
            "closed": ["closed"],
            "noop": ["noop"],
            "failed": ["failed"],
        }
        conditions.append("status = ANY(:statuses)")
        params["statuses"] = api_to_db.get(status, [status])
    result = await session.execute(
        text(
            DELIVERY_SELECT
            + "WHERE "
            + " AND ".join(conditions)
            + " ORDER BY created_at DESC, id DESC LIMIT :limit"
        ),
        params,
    )
    return _rows(result)


async def latest_delivery_for_review(
    session: AsyncSession, *, workspace_id: UUID, review_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            DELIVERY_SELECT
            + "WHERE workspace_id = :ws AND review_id = :review "
            "ORDER BY version DESC, created_at DESC LIMIT 1"
        ),
        {"ws": workspace_id, "review": review_id},
    )
    return _row(result.first())


async def upsert_delivery(
    session: AsyncSession,
    *,
    delivery_id: UUID,
    workspace_id: UUID,
    review_id: UUID,
    version: int,
    target: str,
    provider: str | None,
    branch: str | None,
    base_branch: str | None,
    commit_sha: str | None,
    pr_number: int | None,
    pr_url: str | None,
    status: str,
    merged_commit: str | None,
    merged_at: datetime | None,
    auto_merge: bool,
    error_code: str | None,
    degraded: bool,
    content_hash: str | None,
    manifest: dict[str, Any] | None,
    diff_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO delivery_records "
            "(id, workspace_id, review_id, version, target, provider, branch, base_branch, "
            " commit_sha, pr_number, pr_url, status, merged_commit, merged_at, auto_merge, "
            " error_code, degraded, content_hash, manifest_json, diff_summary_json, attempts) "
            "VALUES (:id, :ws, :review, :version, :target, :provider, :branch, :base, "
            "        :sha, :pr, :url, :status, :merged_commit, :merged_at, :auto_merge, "
            "        :error, :degraded, :hash, CAST(:manifest AS jsonb), "
            "        CAST(:diff AS jsonb), 1) "
            "ON CONFLICT (workspace_id, review_id, version, target) DO UPDATE SET "
            "  provider = EXCLUDED.provider, branch = EXCLUDED.branch, "
            "  base_branch = EXCLUDED.base_branch, commit_sha = EXCLUDED.commit_sha, "
            "  pr_number = EXCLUDED.pr_number, pr_url = EXCLUDED.pr_url, "
            "  status = EXCLUDED.status, merged_commit = EXCLUDED.merged_commit, "
            "  merged_at = EXCLUDED.merged_at, auto_merge = EXCLUDED.auto_merge, "
            "  error_code = EXCLUDED.error_code, degraded = EXCLUDED.degraded, "
            "  content_hash = EXCLUDED.content_hash, manifest_json = EXCLUDED.manifest_json, "
            "  diff_summary_json = EXCLUDED.diff_summary_json, "
            "  attempts = delivery_records.attempts + 1, updated_at = now() "
            "RETURNING id, status"
        ),
        {
            "id": delivery_id,
            "ws": workspace_id,
            "review": review_id,
            "version": version,
            "target": target,
            "provider": provider,
            "branch": branch,
            "base": base_branch,
            "sha": commit_sha,
            "pr": pr_number,
            "url": pr_url,
            "status": status,
            "merged_commit": merged_commit,
            "merged_at": merged_at,
            "auto_merge": auto_merge,
            "error": error_code,
            "degraded": degraded,
            "hash": content_hash,
            "manifest": json.dumps(manifest, ensure_ascii=False, default=str)
            if manifest is not None
            else None,
            "diff": json.dumps(diff_summary, ensure_ascii=False)
            if diff_summary is not None
            else None,
        },
    )
    return dict(result.one()._mapping)


async def mark_merged(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    delivery_id: UUID,
    merged_commit: str | None,
) -> bool:
    result = await session.execute(
        text(
            "UPDATE delivery_records SET status = 'merged', merged_commit = :sha, "
            "  merged_at = now(), updated_at = now() "
            "WHERE workspace_id = :ws AND id = :id "
            "  AND status IN ('pr_open','committed','pending') RETURNING id"
        ),
        {"ws": workspace_id, "id": delivery_id, "sha": merged_commit},
    )
    return result.first() is not None


async def find_by_branch(
    session: AsyncSession, *, workspace_id: UUID, branch: str
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            DELIVERY_SELECT
            + "WHERE workspace_id = :ws AND branch = :branch "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"ws": workspace_id, "branch": branch},
    )
    return _row(result.first())


async def set_review_delivery_status(
    session: AsyncSession, *, workspace_id: UUID, review_id: UUID, status: str
) -> None:
    await session.execute(
        text(
            "UPDATE reviews SET delivery_status = :status, updated_at = now() "
            "WHERE workspace_id = :ws AND id = :id"
        ),
        {"status": status, "ws": workspace_id, "id": review_id},
    )


async def set_version_git_sha(
    session: AsyncSession, *, workspace_id: UUID, review_id: UUID, version: int, sha: str
) -> None:
    await session.execute(
        text(
            "UPDATE review_versions SET git_sha = :sha "
            "WHERE workspace_id = :ws AND review_id = :id AND version = :version"
        ),
        {"sha": sha, "ws": workspace_id, "id": review_id, "version": version},
    )


async def insert_event(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    provider: str,
    event: str,
    payload: dict[str, Any],
    status: str = "handled",
) -> None:
    await session.execute(
        text(
            "INSERT INTO delivery_events "
            "(id, workspace_id, provider_delivery_id, event, payload_digest, status) "
            "VALUES (:id, :ws, :pid, :event, :digest, :status) ON CONFLICT DO NOTHING"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "pid": f"internal:{event}:{uuid7()}",
            "event": event,
            "digest": json.dumps(payload, sort_keys=True, default=str)[:64],
            "status": status,
        },
    )


async def get_topic_file(
    session: AsyncSession, *, workspace_id: UUID, topic: str
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT topic, path, auto_region_hash, status, last_review_id, last_version "
            "FROM topic_files WHERE workspace_id = :ws AND topic = :topic"
        ),
        {"ws": workspace_id, "topic": topic},
    )
    return _row(result.first())


async def upsert_topic_file(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    topic: str,
    path: str,
    auto_region_hash: str | None,
    status: str,
    review_id: UUID | None,
    version: int | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO topic_files "
            "(id, workspace_id, topic, path, auto_region_hash, status, "
            " last_review_id, last_version, updated_at) "
            "VALUES (:id, :ws, :topic, :path, :hash, :status, :review, :version, now()) "
            "ON CONFLICT (workspace_id, topic) DO UPDATE SET "
            "  path = EXCLUDED.path, auto_region_hash = EXCLUDED.auto_region_hash, "
            "  status = EXCLUDED.status, last_review_id = EXCLUDED.last_review_id, "
            "  last_version = EXCLUDED.last_version, updated_at = now()"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "topic": topic,
            "path": path,
            "hash": auto_region_hash,
            "status": status,
            "review": review_id,
            "version": version,
        },
    )


async def latest_reviews_by_kind(
    session: AsyncSession, *, workspace_id: UUID
) -> dict[str, dict[str, Any] | None]:
    result = await session.execute(
        text(
            "SELECT DISTINCT ON (kind) kind, id, period_key, version, status "
            "FROM reviews WHERE workspace_id = :ws AND status IN ('delivered','approved') "
            "ORDER BY kind, period_key DESC, version DESC"
        ),
        {"ws": workspace_id},
    )
    rows = {row["kind"]: dict(row) for row in _rows(result)}
    return {kind: rows.get(kind) for kind in ("daily", "monthly", "yearly")}


async def upsert_review_search(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID,
    version: int,
    kind: str,
    period_key: str,
    title: str,
    path: str,
    content: str,
    content_hash: str,
    occurred_on,
) -> None:
    await session.execute(
        text(
            "UPDATE review_search SET is_current = false "
            "WHERE workspace_id = :ws AND kind = :kind AND period_key = :period "
            "AND is_current"
        ),
        {"ws": workspace_id, "kind": kind, "period": period_key},
    )
    search_text = f"{title}\n{content}"
    await session.execute(
        text(
            "INSERT INTO review_search "
            "(id, workspace_id, review_id, version, kind, period_key, title, path, "
            " search_text, occurred_on, content_hash, is_current) "
            "VALUES (:id, :ws, :review, :version, :kind, :period, :title, :path, "
            "        :text, :occurred, :hash, true) "
            "ON CONFLICT (review_id, version) DO UPDATE SET "
            "  title = EXCLUDED.title, path = EXCLUDED.path, "
            "  search_text = EXCLUDED.search_text, occurred_on = EXCLUDED.occurred_on, "
            "  content_hash = EXCLUDED.content_hash, is_current = true"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "review": review_id,
            "version": version,
            "kind": kind,
            "period": period_key,
            "title": title,
            "path": path,
            "text": search_text,
            "occurred": occurred_on,
            "hash": content_hash,
        },
    )
