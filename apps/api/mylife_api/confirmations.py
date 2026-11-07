"""`create_review` 的确认令牌（API-CONTRACTS §5.7；AC-25）。

token = `v1.{expires_ts}.{hmac}`，HMAC 覆盖
`workspace_id|user_id|kind|period_key|expires_ts`；单次消费记录在
`review_confirmations`（按 sha256(token) 存储）。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api.ids import uuid7
from mylife_api.settings import settings

TOKEN_TTL_SECONDS = 600


class TokenError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _signature(payload: str) -> str:
    return hmac.new(
        settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(
    *, workspace_id: UUID, user_id: UUID, kind: str, period_key: str
) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(seconds=TOKEN_TTL_SECONDS)
    expires_ts = int(expires_at.timestamp())
    payload = f"{workspace_id}|{user_id}|{kind}|{period_key}|{expires_ts}"
    return f"v1.{expires_ts}.{_signature(payload)}", expires_at


def verify_token(
    token: str,
    *,
    workspace_id: UUID,
    user_id: UUID,
    kind: str,
    period_key: str,
) -> datetime:
    """校验签名/TTL/绑定上下文；返回过期时间。"""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise TokenError("TOKEN_MALFORMED")
    try:
        expires_ts = int(parts[1])
    except ValueError as exc:
        raise TokenError("TOKEN_MALFORMED") from exc
    if expires_ts < int(time.time()):
        raise TokenError("TOKEN_EXPIRED")
    payload = f"{workspace_id}|{user_id}|{kind}|{period_key}|{expires_ts}"
    if not hmac.compare_digest(parts[2], _signature(payload)):
        raise TokenError("TOKEN_INVALID")
    return datetime.fromtimestamp(expires_ts, UTC)


async def store_token(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    kind: str,
    period_key: str,
    token: str,
    expires_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO review_confirmations "
            "(id, workspace_id, user_id, token_hash, kind, period_key, expires_at) "
            "VALUES (:id, :ws, :uid, :hash, :kind, :period, :expires) "
            "ON CONFLICT (token_hash) DO NOTHING"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "uid": user_id,
            "hash": token_hash(token),
            "kind": kind,
            "period": period_key,
            "expires": expires_at,
        },
    )


async def consume_token(
    session: AsyncSession,
    *,
    token: str,
    workspace_id: UUID,
    user_id: UUID,
    kind: str,
    period_key: str,
) -> None:
    """标记已消费；重复消费或绑定不符 → TokenError。"""
    verify_token(
        token,
        workspace_id=workspace_id,
        user_id=user_id,
        kind=kind,
        period_key=period_key,
    )
    result = await session.execute(
        text(
            "UPDATE review_confirmations SET consumed_at = now() "
            "WHERE token_hash = :hash AND workspace_id = :ws AND user_id = :uid "
            "  AND kind = :kind AND period_key = :period "
            "  AND consumed_at IS NULL AND expires_at > now() "
            "RETURNING id"
        ),
        {
            "hash": token_hash(token),
            "ws": workspace_id,
            "uid": user_id,
            "kind": kind,
            "period": period_key,
        },
    )
    if result.first() is None:
        raise TokenError("TOKEN_CONSUMED_OR_UNKNOWN")


async def is_consumed(
    session: AsyncSession,
    *,
    token: str,
    workspace_id: UUID,
    user_id: UUID,
    kind: str,
    period_key: str,
) -> bool:
    result = await session.execute(
        text(
            "SELECT consumed_at FROM review_confirmations WHERE token_hash = :hash "
            "AND workspace_id = :ws AND user_id = :uid AND kind = :kind "
            "AND period_key = :period AND expires_at > now()"
        ),
        {
            "hash": token_hash(token),
            "ws": workspace_id,
            "uid": user_id,
            "kind": kind,
            "period": period_key,
        },
    )
    row = result.first()
    return row is not None and row[0] is not None
