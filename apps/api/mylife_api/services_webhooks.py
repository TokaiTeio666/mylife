"""Git webhook 验签与幂等（SEC-16 / SEC-17）。

- 必须对**原始字节**校验签名，不得 JSON 反序列化后重序列化；
- 签名失败不写任何业务表，只写审计；
- `provider_delivery_id` 唯一，重复投递返回 200 + handled=false。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from mylife_contract.errors import ErrorCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api.errors import AppError
from mylife_api.ids import uuid7

logger = logging.getLogger("mylife.api.webhook")

SIGNATURE_TOLERANCE_SECONDS = 300
SUPPORTED_PROVIDERS = {"github", "gitlab"}


@dataclass(frozen=True, slots=True)
class WebhookResult:
    received: bool
    delivery_id: str
    handled: bool
    event: str


def verify_github_signature(
    raw_body: bytes, signature_header: str | None, secret: str
) -> None:
    """`X-Hub-Signature-256: sha256=<hex>`；常量时间比较。"""
    if not signature_header or not secret:
        raise AppError(ErrorCode.WEBHOOK_SIGNATURE_INVALID)
    provided = signature_header.strip()
    if provided.startswith("sha256="):
        provided = provided[len("sha256=") :]
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise AppError(ErrorCode.WEBHOOK_SIGNATURE_INVALID)


def verify_gitlab_token(token_header: str | None, secret: str) -> None:
    if not token_header or not secret:
        raise AppError(ErrorCode.WEBHOOK_SIGNATURE_INVALID)
    if not hmac.compare_digest(token_header.strip(), secret):
        raise AppError(ErrorCode.WEBHOOK_SIGNATURE_INVALID)


def verify_provider(provider: str, raw_body: bytes, headers: dict[str, str], secret: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise AppError(ErrorCode.GIT_PROVIDER_UNSUPPORTED)
    if provider == "github":
        verify_github_signature(raw_body, headers.get("x-hub-signature-256"), secret)
        timestamp = headers.get("x-github-delivery-timestamp")
        if timestamp:
            try:
                sent_at = int(timestamp)
            except ValueError:
                raise AppError(ErrorCode.WEBHOOK_SIGNATURE_INVALID) from None
            if abs(time.time() - sent_at) > SIGNATURE_TOLERANCE_SECONDS:
                raise AppError(ErrorCode.WEBHOOK_SIGNATURE_INVALID)
    else:
        verify_gitlab_token(headers.get("x-gitlab-token"), secret)


def delivery_id_for(provider: str, headers: dict[str, str], raw_body: bytes) -> str:
    if provider == "github":
        value = headers.get("x-github-delivery")
        if value:
            return value
    elif provider == "gitlab":
        value = headers.get("x-gitlab-event-uuid")
        if value:
            return value
    return hashlib.sha256(raw_body).hexdigest()[:32]


def event_name(provider: str, headers: dict[str, str]) -> str:
    if provider == "github":
        return headers.get("x-github-event") or "unknown"
    return headers.get("x-gitlab-event") or "unknown"


async def record_delivery_event(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    provider_delivery_id: str,
    event: str,
    raw_body: bytes,
    status: str = "received",
) -> bool:
    """幂等落库；返回 True 表示首次插入。"""
    result = await session.execute(
        text(
            "INSERT INTO delivery_events "
            "(id, workspace_id, provider_delivery_id, event, payload_digest, status) "
            "VALUES (:id, :ws, :pid, :event, :digest, :status) "
            "ON CONFLICT (provider_delivery_id) DO NOTHING RETURNING id"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "pid": provider_delivery_id,
            "event": event,
            "digest": hashlib.sha256(raw_body).hexdigest(),
            "status": status,
        },
    )
    return result.first() is not None


async def mark_handled(
    session: AsyncSession, *, provider_delivery_id: str, status: str
) -> None:
    await session.execute(
        text(
            "UPDATE delivery_events SET status = :status "
            "WHERE provider_delivery_id = :pid AND status = 'received'"
        ),
        {"status": status, "pid": provider_delivery_id},
    )


def parse_event(provider: str, raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def phase3_disposition(provider: str, event: str, payload: dict[str, Any]) -> str:
    """Phase 3 只做验签与幂等；交付回写属 Phase 4（PHASE3-SPEC D5）。"""
    if provider == "github" and event == "ping":
        return "handled"
    if provider == "github" and event == "pull_request":
        action = payload.get("action")
        merged = (payload.get("pull_request") or {}).get("merged")
        if action == "closed" and merged:
            return "ignored"
    return "ignored"


__all__ = [
    "WebhookResult",
    "delivery_id_for",
    "event_name",
    "mark_handled",
    "parse_event",
    "phase3_disposition",
    "record_delivery_event",
    "verify_provider",
]
