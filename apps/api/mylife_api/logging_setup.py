"""结构化日志（NFR-5 / AC-60）。

每行 JSON 至少含 `ts/level/service/trace_id/workspace_id/run_id/event`；
正文、quote、token、密钥一律不得出现在日志里（SEC-19）。
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
workspace_id_ctx: ContextVar[str] = ContextVar("workspace_id", default="")
run_id_ctx: ContextVar[str] = ContextVar("run_id", default="")

#: 可能承载正文的字段名；出现即丢弃（避免误记证据/消息内容）。
_SENSITIVE_KEYS = frozenset(
    {
        "content",
        "quote",
        "snippet",
        "message",
        "prompt",
        "api_key",
        "token",
        "password",
        "secret",
        "authorization",
    }
)

_STANDARD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
    }
)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "trace_id": trace_id_ctx.get() or getattr(record, "trace_id", "") or "",
            "workspace_id": workspace_id_ctx.get()
            or str(getattr(record, "workspace_id", "") or ""),
            "run_id": run_id_ctx.get() or str(getattr(record, "run_id", "") or ""),
            "event": getattr(record, "event", None) or record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            if key in _SENSITIVE_KEYS or key.lower() in _SENSITIVE_KEYS:
                continue
            if key in {"trace_id", "workspace_id", "run_id", "event"}:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = str(value)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", service: str = "mylife-api") -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def bind_run_context(*, trace_id: str, workspace_id: str, run_id: str) -> None:
    trace_id_ctx.set(trace_id or "")
    workspace_id_ctx.set(workspace_id or "")
    run_id_ctx.set(run_id or "")


def clear_run_context() -> None:
    trace_id_ctx.set("")
    workspace_id_ctx.set("")
    run_id_ctx.set("")
