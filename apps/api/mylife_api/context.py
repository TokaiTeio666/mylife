"""请求级上下文（contextvar）。"""

from __future__ import annotations

from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="req_unknown")


def current_request_id() -> str:
    return request_id_ctx.get()
