"""请求级上下文（contextvar）。"""

from __future__ import annotations

from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="req_unknown")

#: 客户端 IP。由中间件按请求设置，`insert_audit` 在调用方未显式传入时兜底使用，
#: 这样 AC-07 要求的 `ip` 字段不会因为某个服务层忘记透传而缺失。
client_ip_ctx: ContextVar[str | None] = ContextVar("client_ip", default=None)


def current_request_id() -> str:
    return request_id_ctx.get()


def current_client_ip() -> str | None:
    return client_ip_ctx.get()
