"""Git webhook 接收端（API-CONTRACTS §3.12；SEC-16/17）。

Phase 3 只做验签、幂等落库与审计；`delivery_records` 回写属 Phase 4。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from mylife_contract.errors import ErrorCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import services_webhooks
from mylife_api.deps import RunContext, get_db, get_run_context
from mylife_api.errors import AppError
from mylife_api.repositories import insert_audit
from mylife_api.settings import settings

logger = logging.getLogger("mylife.api.webhook")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _resolve_workspace(
    session: AsyncSession, *, provider: str, repo_full_name: str | None
) -> Any:
    """按仓库全名定位 Git 连接；找不到时返回 None（只记审计，不写业务表）。"""
    if not repo_full_name:
        return None
    # 匿名请求无租户上下文：走 SECURITY DEFINER 函数只取定位与密钥两列。
    result = await session.execute(
        text(
            "SELECT workspace_id, webhook_secret_ciphertext "
            "FROM webhook_connection_lookup(:provider, :repo)"
        ),
        {"provider": provider, "repo": repo_full_name},
    )
    return result.first()


def _secret_for(row: Any) -> str:
    if row is not None and row[1] is not None:
        from mylife_api.crypto import decrypt_credential

        try:
            return decrypt_credential(
                bytes(row[1]),
                workspace_id=str(row[0]),
                provider="git-webhook",
            )
        except Exception:  # noqa: BLE001 - 解密失败按未配置处理
            logger.warning("webhook secret decrypt failed")
    return settings.webhook_secret or ""


@router.post("/git/{provider}")
async def receive_git_webhook(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    raw_body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}
    event = services_webhooks.event_name(provider, headers)
    delivery_id = services_webhooks.delivery_id_for(provider, headers, raw_body)
    payload = services_webhooks.parse_event(provider, raw_body)
    repo_full_name = (
        (payload.get("repository") or {}).get("full_name")
        if isinstance(payload.get("repository"), dict)
        else None
    )

    row = await _resolve_workspace(
        db, provider=provider, repo_full_name=repo_full_name
    )
    secret = _secret_for(row)
    try:
        services_webhooks.verify_provider(provider, raw_body, headers, secret)
    except AppError:
        # 签名失败：只写审计，不写任何业务表（SEC-16）。
        # 审计行带 workspace_id 时同样受 RLS 约束，先绑定上下文。
        if row is not None:
            from mylife_api.db import set_rls_context

            await set_rls_context(db, row[0], None)
        await insert_audit(
            db,
            workspace_id=row[0] if row is not None else None,
            user_id=None,
            action="webhook.rejected",
            target=delivery_id,
            detail={"provider": provider, "event": event},
        )
        raise

    if row is None:
        # 未知仓库：验签通过但无归属，按 ignored 记录审计。
        await insert_audit(
            db,
            workspace_id=None,
            user_id=None,
            action="webhook.unrouted",
            target=delivery_id,
            detail={"provider": provider, "event": event},
        )
        return {
            "received": True,
            "delivery_id": delivery_id,
            "handled": False,
            "event": event,
        }

    workspace_id = row[0]
    # 验签通过后绑定租户上下文，才能写 delivery_events（RLS）。
    from mylife_api.db import set_rls_context

    await set_rls_context(db, workspace_id, None)
    first = await services_webhooks.record_delivery_event(
        db,
        workspace_id=workspace_id,
        provider_delivery_id=delivery_id,
        event=event,
        raw_body=raw_body,
    )
    if not first:
        # 重放：返回 200 + handled=false，不重复回写（SEC-17）。
        return {
            "received": True,
            "delivery_id": delivery_id,
            "handled": False,
            "event": event,
        }

    disposition = await services_webhooks.handle_event(
        db,
        workspace_id=workspace_id,
        provider=provider,
        event=event,
        payload=payload,
    )
    await services_webhooks.mark_handled(
        db, provider_delivery_id=delivery_id, status=disposition
    )
    await db.commit()
    return {
        "received": True,
        "delivery_id": delivery_id,
        "handled": disposition == "handled",
        "event": event,
    }


@router.get("/deliveries")
async def list_webhook_deliveries(
    limit: int = 20,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """排障用：仅 owner/admin；RLS 限制为当前 workspace。"""
    if ctx.role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    if limit < 1 or limit > 100:
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "limit"})
    result = await db.execute(
        text(
            "SELECT provider_delivery_id, event, status, received_at "
            "FROM delivery_events ORDER BY received_at DESC LIMIT :limit"
        ),
        {"limit": limit},
    )
    return {
        "items": [
            {
                "provider_delivery_id": row.provider_delivery_id,
                "event": row.event,
                "status": row.status,
                "received_at": row.received_at.isoformat(),
            }
            for row in result
        ],
        "next_cursor": None,
    }
