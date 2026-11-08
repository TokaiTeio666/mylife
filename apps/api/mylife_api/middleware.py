"""HTTP 中间件：request_id、CSRF、安全响应头。

`RequestIdMiddleware` 用纯 ASGI 实现：Starlette 的 `BaseHTTPMiddleware` 会在独立
task 中运行下游，导致 contextvar 无法可靠传播（表现为错误信封里的 `req_unknown`）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from mylife_contract.constants import (
    API_PREFIX,
    API_VERSION,
    COOKIE_CSRF,
    HEADER_API_VERSION,
    HEADER_CSRF,
    HEADER_REQUEST_ID,
)
from mylife_contract.errors import ErrorCode
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mylife_api.context import client_ip_ctx, request_id_ctx
from mylife_api.errors import error_payload
from mylife_api.ids import request_id as new_request_id
from mylife_api.security import SAFE_METHODS, csrf_matches

CSRF_EXEMPT_PREFIXES = ("/api/v1/webhooks/",)


def _client_ip(scope: Scope) -> str | None:
    """与 `deps.client_ip` 同口径：优先 X-Forwarded-For 首段。"""
    headers = Headers(scope=scope)
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    client = scope.get("client")
    return client[0] if client else None


class RequestIdMiddleware:
    """为每个请求分配/回显 `X-Request-Id`（`req_` + ULID）。纯 ASGI。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(HEADER_REQUEST_ID)
        rid = incoming if incoming and incoming.startswith("req_") else new_request_id()
        token = request_id_ctx.set(rid)
        ip_token = client_ip_ctx.set(_client_ip(scope))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[HEADER_REQUEST_ID] = rid
                headers[HEADER_API_VERSION] = API_VERSION
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            request_id_ctx.reset(token)
            client_ip_ctx.reset(ip_token)


class CsrfMiddleware(BaseHTTPMiddleware):
    """double-submit CSRF（API-CONTRACTS §1.3）。

    适用所有非安全方法；`/auth/login`、`/auth/register` 也要求。
    webhook 使用签名校验，豁免。
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if (
            path.startswith(API_PREFIX)
            and request.method not in SAFE_METHODS
            and not path.startswith(CSRF_EXEMPT_PREFIXES)
        ):
            cookie_value = request.cookies.get(COOKIE_CSRF)
            header_value = request.headers.get(HEADER_CSRF)
            if not csrf_matches(cookie_value, header_value):
                return JSONResponse(
                    status_code=403,
                    content=error_payload(ErrorCode.AUTH_CSRF_INVALID),
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/v1/auth") or request.url.path.startswith(
            "/api/v1/settings"
        ):
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response


def install_middlewares(app: ASGIApp) -> None:  # type: ignore[override]
    # 后添加的最先执行；顺序：RequestId → CSRF → SecurityHeaders。
    app.add_middleware(SecurityHeadersMiddleware)  # type: ignore[attr-defined]
    app.add_middleware(CsrfMiddleware)  # type: ignore[attr-defined]
    app.add_middleware(RequestIdMiddleware)  # type: ignore[attr-defined]
