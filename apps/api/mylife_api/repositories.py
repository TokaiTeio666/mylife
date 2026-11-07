"""数据访问层（SQLAlchemy Core + 显式 SQL）。

约定：
- 任何含 `workspace_id` 的查询都必须在事务内先 `set_rls_context`；
- SQL 中不拼接用户输入，全部参数化；
- 跨租户访问统一由 RLS 返回 0 行，应用层再转 404（SEC-06/07）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row._mapping) if row is not None else None


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# users（平台级，无 RLS）
# ---------------------------------------------------------------------------


async def get_user_by_email(session: AsyncSession, email: str) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, email, password_hash, display_name, status, is_platform_admin, created_at "
            "FROM users WHERE lower(email) = lower(:email)"
        ),
        {"email": email},
    )
    return _row(result.first())


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, email, password_hash, display_name, status, is_platform_admin, created_at "
            "FROM users WHERE id = :uid"
        ),
        {"uid": user_id},
    )
    return _row(result.first())


async def insert_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    email: str,
    password_hash: str,
    display_name: str,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO users (id, email, password_hash, display_name) "
            "VALUES (:id, :email, :hash, :name) "
            "RETURNING id, email, display_name, status, created_at"
        ),
        {"id": user_id, "email": email, "hash": password_hash, "name": display_name},
    )
    return dict(result.one()._mapping)


async def touch_user(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(
        text("UPDATE users SET updated_at = now() WHERE id = :uid"), {"uid": user_id}
    )


# ---------------------------------------------------------------------------
# sessions（平台级，无 RLS；只通过 token_hash 访问）
# ---------------------------------------------------------------------------


async def insert_session(
    session: AsyncSession,
    *,
    session_id: UUID,
    user_id: UUID,
    token_hash: str,
    active_workspace_id: UUID | None,
    expires_at: datetime,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO sessions (id, user_id, token_hash, active_workspace_id, expires_at) "
            "VALUES (:id, :uid, :hash, :ws, :expires) "
            "RETURNING id, user_id, token_version, active_workspace_id, expires_at"
        ),
        {
            "id": session_id,
            "uid": user_id,
            "hash": token_hash,
            "ws": active_workspace_id,
            "expires": expires_at,
        },
    )
    return dict(result.one()._mapping)


async def get_session_by_token_hash(
    session: AsyncSession, token_hash: str
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT s.id, s.user_id, s.token_hash, s.token_version, s.active_workspace_id, "
            "       s.expires_at, s.revoked_at, s.created_at "
            "FROM sessions s WHERE s.token_hash = :hash"
        ),
        {"hash": token_hash},
    )
    return _row(result.first())


async def revoke_session(session: AsyncSession, session_id: UUID) -> None:
    await session.execute(
        text("UPDATE sessions SET revoked_at = now() WHERE id = :sid AND revoked_at IS NULL"),
        {"sid": session_id},
    )


async def revoke_all_sessions(session: AsyncSession, user_id: UUID) -> int:
    """退出所有设备：token_version += 1 且 revoked_at 置位（FR-A6 / AC-06）。"""
    result = await session.execute(
        text(
            "UPDATE sessions SET token_version = token_version + 1, revoked_at = now() "
            "WHERE user_id = :uid AND revoked_at IS NULL"
        ),
        {"uid": user_id},
    )
    return result.rowcount or 0


async def set_active_workspace(
    session: AsyncSession, session_id: UUID, workspace_id: UUID
) -> None:
    await session.execute(
        text("UPDATE sessions SET active_workspace_id = :ws WHERE id = :sid"),
        {"ws": workspace_id, "sid": session_id},
    )


# ---------------------------------------------------------------------------
# workspaces / workspace_members
# ---------------------------------------------------------------------------


async def insert_workspace(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    name: str,
    slug: str,
    owner_id: UUID,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO workspaces (id, name, slug, owner_id) "
            "VALUES (:id, :name, :slug, :owner) "
            "RETURNING id, name, slug, owner_id, plan, created_at"
        ),
        {"id": workspace_id, "name": name, "slug": slug, "owner": owner_id},
    )
    return dict(result.one()._mapping)


async def insert_member(
    session: AsyncSession, *, workspace_id: UUID, user_id: UUID, role: str = "owner"
) -> None:
    await session.execute(
        text(
            "INSERT INTO workspace_members (workspace_id, user_id, role) "
            "VALUES (:ws, :uid, :role)"
        ),
        {"ws": workspace_id, "uid": user_id, "role": role},
    )


async def insert_default_settings(session: AsyncSession, workspace_id: UUID) -> None:
    await session.execute(
        text(
            "INSERT INTO workspace_settings (workspace_id) VALUES (:ws) "
            "ON CONFLICT (workspace_id) DO NOTHING"
        ),
        {"ws": workspace_id},
    )


async def insert_default_schedules(session: AsyncSession, workspace_id: UUID) -> None:
    await session.execute(
        text(
            """
            INSERT INTO workspace_schedules (workspace_id, kind, local_time, timezone)
            VALUES (:ws, 'daily', '01:00', 'Asia/Shanghai'),
                   (:ws, 'weekly', '01:30', 'Asia/Shanghai'),
                   (:ws, 'yearly', '02:00', 'Asia/Shanghai')
            ON CONFLICT (workspace_id, kind) DO NOTHING
            """
        ),
        {"ws": workspace_id},
    )


async def list_user_workspaces(
    session: AsyncSession, user_id: UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT w.id, w.name, w.slug, w.plan, w.created_at, wm.role
            FROM workspaces w
            JOIN workspace_members wm ON wm.workspace_id = w.id
            WHERE wm.user_id = :uid
            ORDER BY w.created_at ASC, w.id ASC
            """
        ),
        {"uid": user_id},
    )
    return _rows(result)


async def get_membership(
    session: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT workspace_id, user_id, role, joined_at FROM workspace_members "
            "WHERE workspace_id = :ws AND user_id = :uid"
        ),
        {"ws": workspace_id, "uid": user_id},
    )
    return _row(result.first())


async def get_workspace(session: AsyncSession, workspace_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, name, slug, owner_id, plan, created_at FROM workspaces WHERE id = :ws"
        ),
        {"ws": workspace_id},
    )
    return _row(result.first())


async def rename_workspace(session: AsyncSession, workspace_id: UUID, name: str) -> None:
    await session.execute(
        text("UPDATE workspaces SET name = :name WHERE id = :ws"),
        {"name": name, "ws": workspace_id},
    )


async def get_settings(session: AsyncSession, workspace_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT workspace_id, timezone, daily_at, weekly_at, yearly_at, reviews_root, "
            "       retrieval_top_k, context_window_tokens, web_search_enabled, "
            "       auto_merge_daily, delivery_policy_json, embedding_profile_id "
            "FROM workspace_settings WHERE workspace_id = :ws"
        ),
        {"ws": workspace_id},
    )
    return _row(result.first())


async def count_documents(session: AsyncSession, workspace_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) AS n FROM documents WHERE workspace_id = :ws"), {"ws": workspace_id}
    )
    return int(result.scalar_one())


async def count_reviews(session: AsyncSession, workspace_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) AS n FROM reviews WHERE workspace_id = :ws"), {"ws": workspace_id}
    )
    return int(result.scalar_one())


async def count_open_tasks(session: AsyncSession, workspace_id: UUID) -> int:
    result = await session.execute(
        text(
            "SELECT count(*) AS n FROM tasks "
            "WHERE workspace_id = :ws AND status IN ('open','carried')"
        ),
        {"ws": workspace_id},
    )
    return int(result.scalar_one())


async def slug_exists(session: AsyncSession, slug: str) -> bool:
    result = await session.execute(
        text("SELECT 1 FROM workspaces WHERE slug = :slug"), {"slug": slug}
    )
    return result.first() is not None


async def count_memberships(session: AsyncSession, user_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) AS n FROM workspace_members WHERE user_id = :uid"), {"uid": user_id}
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# invite_codes（平台级）
# ---------------------------------------------------------------------------


async def get_invite_for_update(session: AsyncSession, code: str) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, code, created_by, used_by, expires_at, used_at "
            "FROM invite_codes WHERE code = :code FOR UPDATE"
        ),
        {"code": code},
    )
    return _row(result.first())


async def mark_invite_used(session: AsyncSession, *, code_id: UUID, user_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE invite_codes SET used_by = :uid, used_at = now() "
            "WHERE id = :id AND used_at IS NULL"
        ),
        {"uid": user_id, "id": code_id},
    )


async def insert_invite_codes(
    session: AsyncSession,
    *,
    created_by: UUID,
    codes: list[tuple[str, datetime | None]],
    note: str | None,
) -> list[dict[str, Any]]:
    inserted: list[dict[str, Any]] = []
    for code, expires_at in codes:
        result = await session.execute(
            text(
                "INSERT INTO invite_codes (id, code, created_by, expires_at, note) "
                "VALUES (gen_random_uuid(), :code, :by, :expires, :note) "
                "RETURNING id, code, created_by, used_by, expires_at, used_at, note"
            ),
            {"code": code, "by": created_by, "expires": expires_at, "note": note},
        )
        inserted.append(dict(result.one()._mapping))
    return inserted


async def list_invite_codes(
    session: AsyncSession, *, status: str | None, limit: int = 50
) -> list[dict[str, Any]]:
    where = "TRUE"
    params: dict[str, Any] = {"limit": limit}
    if status == "unused":
        where = "used_at IS NULL AND (expires_at IS NULL OR expires_at > now())"
    elif status == "used":
        where = "used_at IS NOT NULL"
    elif status == "expired":
        where = "used_at IS NULL AND expires_at IS NOT NULL AND expires_at <= now()"
    result = await session.execute(
        text(
            f"SELECT id, code, created_by, used_by, expires_at, used_at, note, "
            f"       CASE WHEN used_at IS NOT NULL THEN 'used' "
            f"            WHEN expires_at IS NOT NULL AND expires_at <= now() THEN 'expired' "
            f"            ELSE 'unused' END AS status "
            f"FROM invite_codes WHERE {where} ORDER BY id DESC LIMIT :limit"
        ),
        params,
    )
    return _rows(result)


# ---------------------------------------------------------------------------
# documents（Phase 0 仅目录树）
# ---------------------------------------------------------------------------


async def list_documents(
    session: AsyncSession, workspace_id: UUID, *, include_generated: bool = False
) -> list[dict[str, Any]]:
    if include_generated:
        where = "workspace_id = :ws"
    else:
        where = "workspace_id = :ws AND source_type = 'user'"
    result = await session.execute(
        text(
            f"SELECT id, path, title, size, mtime, occurred_on, tags, source_type "
            f"FROM documents WHERE {where} ORDER BY path ASC"
        ),
        {"ws": workspace_id},
    )
    return _rows(result)


# ---------------------------------------------------------------------------
# audit_logs（append-only）
# ---------------------------------------------------------------------------


async def insert_audit(
    session: AsyncSession,
    *,
    workspace_id: UUID | None,
    user_id: UUID | None,
    action: str,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """审计写入与业务同事务（API-CONTRACTS §1.13）。

    `detail` 由调用方保证不含密钥与正文（SEC-19）；此处再做一次长度裁剪。
    """
    safe_detail = dict(detail or {})
    for key, value in list(safe_detail.items()):
        if isinstance(value, str) and len(value) > 200:
            safe_detail[key] = value[:200] + "…"
    await session.execute(
        text(
            "INSERT INTO audit_logs (id, workspace_id, user_id, action, target, detail, ip) "
            "VALUES (gen_random_uuid(), :ws, :uid, :action, :target, "
            "        CAST(:detail AS jsonb), :ip)"
        ),
        {
            "ws": workspace_id,
            "uid": user_id,
            "action": action,
            "target": target,
            "detail": json.dumps(safe_detail, ensure_ascii=False),
            "ip": ip,
        },
    )


async def count_audit_actions(session: AsyncSession, actions: list[str]) -> dict[str, int]:
    result = await session.execute(
        text(
            "SELECT action, count(*) AS n FROM audit_logs "
            "WHERE action = ANY(:actions) GROUP BY action"
        ),
        {"actions": actions},
    )
    return {r.action: int(r.n) for r in result}


# ---------------------------------------------------------------------------
# provider_credentials（密文列；明文只在内存中出现）
# ---------------------------------------------------------------------------


async def upsert_provider_credential(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    provider: str,
    key_ciphertext: bytes,
    key_version: int,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            INSERT INTO provider_credentials
              (id, workspace_id, provider, key_ciphertext, key_version, status)
            VALUES (gen_random_uuid(), :ws, :provider, :ciphertext, :version, 'active')
            ON CONFLICT (workspace_id, provider) DO UPDATE
              SET key_ciphertext = EXCLUDED.key_ciphertext,
                  key_version = EXCLUDED.key_version,
                  status = 'active',
                  updated_at = now()
            RETURNING id, workspace_id, provider, key_version, status, created_at, updated_at
            """
        ),
        {
            "ws": workspace_id,
            "provider": provider,
            "ciphertext": key_ciphertext,
            "version": key_version,
        },
    )
    return dict(result.one()._mapping)


async def get_provider_credential(
    session: AsyncSession, *, workspace_id: UUID, provider: str
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, workspace_id, provider, key_ciphertext, key_version, status "
            "FROM provider_credentials WHERE workspace_id = :ws AND provider = :provider"
        ),
        {"ws": workspace_id, "provider": provider},
    )
    return _row(result.first())
