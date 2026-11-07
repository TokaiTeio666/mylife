"""设置服务：调度、Git 连接、用量、检索（API-CONTRACTS §3.9.7–§3.9.11）。

Git 凭据只以信封密文落库；连接测试失败不保存（AC-52）。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from mylife_contract.errors import ErrorCode
from mylife_contract.settings import (
    GitConnectionSummary,
    RetrievalSettings,
    RetrievalUpdate,
    ScheduleEnabled,
    ScheduleNextRun,
    ScheduleSettings,
    ScheduleUpdate,
)
from mylife_delivery.providers import ProviderError, provider_for
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories, services_budget
from mylife_api.crypto import encrypt_credential
from mylife_api.db import set_rls_context
from mylife_api.errors import AppError
from mylife_api.ids import uuid7
from mylife_api.repositories import insert_audit
from mylife_api.schedule import next_run_at as compute_next_run
from mylife_api.settings import settings as app_settings

logger = logging.getLogger("mylife.api.settings")

AUTH_WIRE_TO_DB = {"app": "github_app", "deploy_key": "deploy_key", "pat": "pat"}
AUTH_DB_TO_WIRE = {value: key for key, value in AUTH_WIRE_TO_DB.items()}


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------


async def _schedule_rows(session: AsyncSession, workspace_id: UUID) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT kind, local_time, timezone, next_run_at, catchup_days, enabled "
            "FROM workspace_schedules WHERE workspace_id = :ws"
        ),
        {"ws": workspace_id},
    )
    return {row._mapping["kind"]: dict(row._mapping) for row in result}


async def get_schedule(session: AsyncSession, *, workspace_id: UUID) -> ScheduleSettings:
    settings_row = await repositories.get_settings(session, workspace_id) or {}
    rows = await _schedule_rows(session, workspace_id)
    daily = rows.get("daily") or {}
    return ScheduleSettings(
        timezone=settings_row.get("timezone") or daily.get("timezone") or "Asia/Shanghai",
        daily_at=settings_row.get("daily_at") or daily.get("local_time") or "01:00",
        weekly_at=settings_row.get("weekly_at") or "01:30",
        yearly_at=settings_row.get("yearly_at") or "02:00",
        catchup_days=int(daily.get("catchup_days") or 7),
        enabled=ScheduleEnabled(
            daily=bool((rows.get("daily") or {}).get("enabled", True)),
            weekly=bool((rows.get("weekly") or {}).get("enabled", True)),
            yearly=bool((rows.get("yearly") or {}).get("enabled", True)),
        ),
        next_run_at=ScheduleNextRun(
            daily=(rows.get("daily") or {}).get("next_run_at"),
            weekly=(rows.get("weekly") or {}).get("next_run_at"),
            yearly=(rows.get("yearly") or {}).get("next_run_at"),
        ),
    )


async def put_schedule(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
    payload: ScheduleUpdate,
) -> ScheduleSettings:
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    now = datetime.now(UTC)
    await session.execute(
        text(
            "UPDATE workspace_settings SET timezone = :tz, daily_at = :daily, "
            "  weekly_at = :weekly, yearly_at = :yearly, updated_at = now() "
            "WHERE workspace_id = :ws"
        ),
        {
            "tz": payload.timezone,
            "daily": payload.daily_at,
            "weekly": payload.weekly_at,
            "yearly": payload.yearly_at,
            "ws": workspace_id,
        },
    )
    values = {
        "daily": (payload.daily_at, payload.enabled.daily),
        "weekly": (payload.weekly_at, payload.enabled.weekly),
        "yearly": (payload.yearly_at, payload.enabled.yearly),
    }
    for kind, (local_time, enabled) in values.items():
        next_at = (
            compute_next_run(kind, local_time, payload.timezone, now) if enabled else None
        )
        await session.execute(
            text(
                "INSERT INTO workspace_schedules "
                "(id, workspace_id, kind, local_time, timezone, next_run_at, "
                " catchup_days, enabled) "
                "VALUES (:id, :ws, :kind, :time, :tz, :next_at, :catchup, :enabled) "
                "ON CONFLICT (workspace_id, kind) DO UPDATE SET "
                "  local_time = EXCLUDED.local_time, timezone = EXCLUDED.timezone, "
                "  next_run_at = EXCLUDED.next_run_at, "
                "  catchup_days = EXCLUDED.catchup_days, enabled = EXCLUDED.enabled"
            ),
            {
                "id": uuid7(),
                "ws": workspace_id,
                "kind": kind,
                "time": local_time,
                "tz": payload.timezone,
                "next_at": next_at,
                "catchup": payload.catchup_days,
                "enabled": enabled,
            },
        )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="settings.schedule.updated",
        target=str(workspace_id),
        detail={
            "timezone": payload.timezone,
            "daily_at": payload.daily_at.strftime("%H:%M"),
            "weekly_at": payload.weekly_at.strftime("%H:%M"),
            "yearly_at": payload.yearly_at.strftime("%H:%M"),
            "catchup_days": payload.catchup_days,
        },
    )
    await session.commit()
    await set_rls_context(session, workspace_id, user_id)
    return await get_schedule(session, workspace_id=workspace_id)


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _summary(row: dict[str, Any]) -> GitConnectionSummary:
    credential_status = row.get("credential_status") or "untested"
    if credential_status not in {"valid", "invalid", "untested"}:
        credential_status = "untested"
    auth_type = AUTH_DB_TO_WIRE.get(row.get("auth_type") or "pat", "pat")
    return GitConnectionSummary(
        id=row["id"],
        role=row["role"],
        provider=row["provider"],
        repo_full_name=row["repo_full_name"],
        base_branch=row["base_branch"],
        auth_type=auth_type,
        mode=row.get("mode"),
        reviews_root=row.get("reviews_root"),
        sync_interval_minutes=int(row.get("sync_interval_minutes") or 0),
        sync_enabled=bool(row.get("sync_enabled")),
        next_sync_at=row.get("next_sync_at"),
        last_sync_at=row.get("last_sync_at"),
        last_sync_status=row.get("last_sync_status"),
        credential_status=credential_status,
        last_tested_at=row.get("last_tested_at"),
    )


async def list_git_connections(
    session: AsyncSession, *, workspace_id: UUID
) -> list[GitConnectionSummary]:
    rows = await repositories_repo(session, workspace_id)
    return [_summary(row) for row in rows]


async def repositories_repo(session: AsyncSession, workspace_id: UUID) -> list[dict[str, Any]]:
    from mylife_api import repo_delivery

    return await repo_delivery.list_connections(session, workspace_id=workspace_id)


def _is_local_path(value: str) -> bool:
    return (
        (len(value) > 2 and value[1] == ":")
        or value.startswith("/")
        or value.startswith("\\\\")
    )


def _validate_remote(url: str) -> None:
    if app_settings.allow_local_git and _is_local_path(url):
        return
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details={"field": "remote_url", "reason": "CREDENTIAL_IN_URL"},
        )
    scheme = (parsed.scheme or "").lower()
    if url.startswith("file://"):
        if not app_settings.allow_local_git:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                details={"field": "remote_url", "reason": "SSRF_BLOCKED"},
            )
        return
    if scheme not in {"https", "ssh", "git+ssh"}:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details={"field": "remote_url", "reason": "SSRF_BLOCKED"},
        )
    host = parsed.hostname or ""
    if not host:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, details={"field": "remote_url", "reason": "HOST_MISSING"}
        )
    if app_settings.allow_local_git and host in {"localhost", "127.0.0.1", "::1"}:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise AppError(
            ErrorCode.INGEST_REPO_UNREACHABLE
        ) from exc
    for info in infos:
        try:
            address = info[4][0]
            if not ipaddress.ip_address(address).is_global:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    details={"field": "remote_url", "reason": "SSRF_BLOCKED"},
                )
        except ValueError:
            continue


async def put_git_connection(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
    payload,
) -> GitConnectionSummary:
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    if payload.remote_url:
        _validate_remote(payload.remote_url)
    if payload.api_base_url:
        parsed = urlparse(payload.api_base_url)
        if parsed.scheme not in {"http", "https"}:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                details={"field": "api_base_url", "reason": "SSRF_BLOCKED"},
            )
        host = parsed.hostname or ""
        local_ok = app_settings.allow_local_git and host in {"localhost", "127.0.0.1"}
        if not local_ok and parsed.scheme != "https":
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                details={"field": "api_base_url", "reason": "HTTPS_REQUIRED"},
            )

    existing = await _existing_connection(
        session,
        workspace_id=workspace_id,
        provider=payload.provider,
        repo_full_name=payload.repo_full_name,
        role=payload.role,
    )
    conn_id = existing["id"] if existing else uuid4()
    credential = payload.credential
    if credential is None and existing:
        credential = None  # 保留原密文
    ciphertext = None
    if credential is not None:
        ciphertext = encrypt_credential(
            credential, workspace_id=str(workspace_id), provider=f"git:{conn_id}"
        )

    if payload.provider not in {"github", "gitlab"}:
        raise AppError(ErrorCode.GIT_PROVIDER_UNSUPPORTED)
    if credential is not None or (existing and _decrypt(existing, workspace_id)):
        token = credential if credential is not None else _decrypt(existing, workspace_id)
        provider = provider_for(
            provider=payload.provider,
            repo_full_name=payload.repo_full_name,
            token=token or "",
            api_base_url=payload.api_base_url or (existing or {}).get("api_base_url"),
        )
        try:
            result = await asyncio.to_thread(provider.test_connection)
        except ProviderError as exc:
            raise AppError(
                ErrorCode.INGEST_REPO_UNREACHABLE, details={"reason": exc.reason}
            ) from exc
        if not result.ok:
            raise AppError(
                ErrorCode.INGEST_AUTH_FAILED,
                details={
                    "reason": result.reason,
                    "provider": payload.provider,
                    "role": payload.role,
                },
            )

    reviews_root = payload.reviews_root or (existing or {}).get("reviews_root") or "wiki/复盘"
    sync_enabled = payload.role == "source" and payload.sync_interval_minutes > 0
    next_sync_at = (
        datetime.now(UTC) + timedelta(minutes=payload.sync_interval_minutes)
        if sync_enabled
        else None
    )
    db_auth = AUTH_WIRE_TO_DB[payload.auth_type]
    result = await session.execute(
        text(
            "INSERT INTO git_connections "
            "(id, workspace_id, role, provider, repo_full_name, base_branch, auth_type, "
            " credential_ciphertext, credential_key_version, reviews_root, mode, "
            " sync_enabled, sync_interval_minutes, next_sync_at, remote_url, api_base_url, "
            " credential_status, last_tested_at) "
            "VALUES (:id, :ws, :role, :provider, :repo, :branch, :auth, :cipher, :key_version, "
            "        :root, :mode, :sync_enabled, :interval, :next_sync, :remote, :api_base, "
            "        'valid', now()) "
            "ON CONFLICT (workspace_id, provider, repo_full_name, role) DO UPDATE SET "
            "  base_branch = EXCLUDED.base_branch, auth_type = EXCLUDED.auth_type, "
            "  credential_ciphertext = COALESCE(EXCLUDED.credential_ciphertext, "
            "    git_connections.credential_ciphertext), "
            "  reviews_root = EXCLUDED.reviews_root, mode = EXCLUDED.mode, "
            "  sync_enabled = EXCLUDED.sync_enabled, "
            "  sync_interval_minutes = EXCLUDED.sync_interval_minutes, "
            "  next_sync_at = EXCLUDED.next_sync_at, remote_url = EXCLUDED.remote_url, "
            "  api_base_url = EXCLUDED.api_base_url, credential_status = 'valid', "
            "  last_tested_at = now(), updated_at = now() "
            "RETURNING id, role, provider, repo_full_name, base_branch, auth_type, "
            "  reviews_root, mode, sync_enabled, sync_interval_minutes, next_sync_at, "
            "  last_sync_at, last_sync_status, credential_status, last_tested_at"
        ),
        {
            "id": conn_id,
            "ws": workspace_id,
            "role": payload.role,
            "provider": payload.provider,
            "repo": payload.repo_full_name,
            "branch": payload.base_branch,
            "auth": db_auth,
            "cipher": ciphertext,
            "key_version": 1 if ciphertext is not None else None,
            "root": reviews_root,
            "mode": payload.mode or "managed",
            "sync_enabled": sync_enabled,
            "interval": payload.sync_interval_minutes,
            "next_sync": next_sync_at,
            "remote": payload.remote_url,
            "api_base": payload.api_base_url,
        },
    )
    row = dict(result.one()._mapping)
    if payload.auto_merge_daily is not None:
        await session.execute(
            text(
                "UPDATE workspace_settings SET auto_merge_daily = :value, updated_at = now() "
                "WHERE workspace_id = :ws"
            ),
            {"value": payload.auto_merge_daily, "ws": workspace_id},
        )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="settings.git.updated",
        target=payload.repo_full_name,
        detail={
            "role": payload.role,
            "provider": payload.provider,
            "sync_interval_minutes": payload.sync_interval_minutes,
        },
    )
    await session.commit()
    return _summary(row)


async def _existing_connection(
    session: AsyncSession, *, workspace_id: UUID, provider: str, repo_full_name: str, role: str
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, credential_ciphertext, api_base_url, reviews_root "
            "FROM git_connections WHERE workspace_id = :ws AND provider = :provider "
            "AND repo_full_name = :repo AND role = :role"
        ),
        {"ws": workspace_id, "provider": provider, "repo": repo_full_name, "role": role},
    )
    row = result.first()
    return dict(row._mapping) if row else None


def _decrypt(row: dict[str, Any], workspace_id: UUID) -> str | None:
    blob = row.get("credential_ciphertext")
    if blob is None:
        return None
    from mylife_api.crypto import CredentialDecryptError, decrypt_credential

    for provider in (f"git:{row['id']}", "git"):
        try:
            return decrypt_credential(
                bytes(blob), workspace_id=str(workspace_id), provider=provider
            )
        except CredentialDecryptError:
            continue
    return None


async def delete_git_connection(
    session: AsyncSession, *, workspace_id: UUID, user_id: UUID, role: str
) -> None:
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    result = await session.execute(
        text("DELETE FROM git_connections WHERE workspace_id = :ws RETURNING id"),
        {"ws": workspace_id},
    )
    if result.first() is None:
        raise AppError(ErrorCode.NOT_FOUND)
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="settings.git.removed",
        target=str(workspace_id),
        detail={},
    )
    await session.commit()


# ---------------------------------------------------------------------------
# usage / retrieval
# ---------------------------------------------------------------------------


async def get_usage(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    from_ts: datetime,
    to_ts: datetime,
    group_by: str,
) -> dict[str, Any]:
    condition = "workspace_id = :ws AND created_at >= :from_ts AND created_at < :to_ts"
    params = {"ws": workspace_id, "from_ts": from_ts, "to_ts": to_ts}
    totals_result = await session.execute(
        text(
            "SELECT COALESCE(sum(prompt_tokens),0) AS prompt, "
            "       COALESCE(sum(completion_tokens),0) AS completion, "
            "       count(*) AS calls, COALESCE(sum(cost),0) AS cost, "
            "       count(*) FILTER (WHERE kind = 'review') AS review_runs "
            "FROM usage_events WHERE " + condition
        ),
        params,
    )
    totals = totals_result.one()
    bucket_sql = {
        "day": "to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD')",
        "kind": "kind",
        "model": "model",
    }[group_by]
    series_result = await session.execute(
        text(
            f"SELECT {bucket_sql} AS bucket, "
            "       COALESCE(sum(prompt_tokens),0) AS prompt, "
            "       COALESCE(sum(completion_tokens),0) AS completion, count(*) AS calls "
            "FROM usage_events WHERE " + condition + " GROUP BY 1 ORDER BY 1"
        ),
        params,
    )
    schedule = await repositories.get_settings(session, workspace_id) or {}
    timezone = schedule.get("timezone") or "Asia/Shanghai"
    daily_budget = services_budget.workspace_limits("workspace_daily")
    monthly_budget = services_budget.workspace_limits("workspace_monthly")
    day_start, day_end = services_budget.workspace_window(
        datetime.now(UTC), timezone, "daily"
    )
    month_start, month_end = services_budget.workspace_window(
        datetime.now(UTC), timezone, "monthly"
    )
    daily_usage = await services_budget.workspace_usage(
        session, workspace_id=workspace_id, start=day_start, end=day_end
    )
    monthly_usage = await services_budget.workspace_usage(
        session, workspace_id=workspace_id, start=month_start, end=month_end
    )

    def _budget_state(used: dict[str, Any], budget) -> dict[str, Any]:
        ratio = max(
            used["input_tokens"] / max(1, budget.max_input_tokens),
            used["output_tokens"] / max(1, budget.max_output_tokens),
        )
        state = "exceeded" if ratio >= 1 else ("warn" if ratio >= 0.8 else "ok")
        return {
            "input_tokens": used["input_tokens"],
            "output_tokens": used["output_tokens"],
            "ratio": round(ratio, 4),
            "state": state,
        }
    return {
        "totals": {
            "prompt_tokens": int(totals.prompt),
            "completion_tokens": int(totals.completion),
            "calls": int(totals.calls),
            "review_runs": int(totals.review_runs),
            "cost": round(float(totals.cost), 4),
        },
        "series": [
            {
                "bucket": row.bucket,
                "prompt_tokens": int(row.prompt),
                "completion_tokens": int(row.completion),
                "calls": int(row.calls),
            }
            for row in series_result
        ],
        "budget": {
            "daily": _budget_state(daily_usage, daily_budget),
            "monthly": _budget_state(monthly_usage, monthly_budget),
        },
    }


async def get_retrieval(session: AsyncSession, *, workspace_id: UUID) -> RetrievalSettings:
    row = await repositories.get_settings(session, workspace_id) or {}
    return RetrievalSettings(
        retrieval_top_k=int(row.get("retrieval_top_k") or 20),
        context_window_tokens=int(row.get("context_window_tokens") or 32000),
        web_search_enabled=bool(row.get("web_search_enabled")),
        vector_enabled=app_settings.embedding_provider != "off",
    )


async def put_retrieval(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
    payload: RetrievalUpdate,
) -> RetrievalSettings:
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    if payload.web_search_enabled:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, details={"reason": "FEATURE_DISABLED"}
        )
    await session.execute(
        text(
            "UPDATE workspace_settings SET retrieval_top_k = :top_k, "
            "  context_window_tokens = :ctx, web_search_enabled = false, updated_at = now() "
            "WHERE workspace_id = :ws"
        ),
        {
            "top_k": payload.retrieval_top_k,
            "ctx": payload.context_window_tokens,
            "ws": workspace_id,
        },
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="settings.retrieval.updated",
        target=str(workspace_id),
        detail={"retrieval_top_k": payload.retrieval_top_k},
    )
    await session.commit()
    await set_rls_context(session, workspace_id, user_id)
    return await get_retrieval(session, workspace_id=workspace_id)


__all__ = [
    "delete_git_connection",
    "get_retrieval",
    "get_schedule",
    "get_usage",
    "list_git_connections",
    "put_git_connection",
    "put_retrieval",
    "put_schedule",
]
