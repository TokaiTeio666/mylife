"""统一错误模型与异常处理器（API-CONTRACTS §1.5 / §2）。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mylife_contract.common import WORKSPACE_ID_NOT_ALLOWED
from mylife_contract.errors import ERROR_SPECS, ErrorCode
from starlette.exceptions import HTTPException as StarletteHTTPException

from mylife_api.context import current_request_id

logger = logging.getLogger("mylife.api.errors")


class AppError(Exception):
    """业务异常：由错误码表决定 HTTP 状态、retryable 与默认文案。"""

    def __init__(
        self,
        code: ErrorCode,
        *,
        message: str | None = None,
        details: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.spec = ERROR_SPECS[code]
        self.message = message or self.spec.message
        self.details = details
        self.headers = headers or {}
        super().__init__(self.message)


def error_payload(
    code: ErrorCode,
    *,
    message: str | None = None,
    details: Any | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    spec = ERROR_SPECS[code]
    return {
        "error": {
            "code": spec.code.value,
            "message": message or spec.message,
            "details": details,
            "retryable": spec.retryable,
        },
        "request_id": request_id or current_request_id(),
    }


def _json_error(
    code: ErrorCode,
    *,
    status: int | None = None,
    message: str | None = None,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    spec = ERROR_SPECS[code]
    payload = error_payload(code, message=message, details=details)
    response_headers = dict(headers or {})
    if spec.retryable and "Retry-After" not in response_headers:
        # 无明确等待时间时不臆造；仅当调用方提供时输出。
        pass
    return JSONResponse(
        status_code=status or spec.http_status,
        content=payload,
        headers=response_headers,
    )


def _validation_details(exc: RequestValidationError) -> tuple[list[dict[str, Any]], bool]:
    details: list[dict[str, Any]] = []
    workspace_id_present = False
    for err in exc.errors():
        loc = [
            str(part)
            for part in err.get("loc", ())
            if part not in ("body", "query", "path", "header")
        ]
        field = ".".join(loc) if loc else None
        reason = str(err.get("msg", "invalid value"))
        if (field and field.split(".")[-1] == "workspace_id") or (
            WORKSPACE_ID_NOT_ALLOWED in reason
        ):
            workspace_id_present = True
            reason = WORKSPACE_ID_NOT_ALLOWED
        details.append({"field": field, "reason": reason})
    return details, workspace_id_present


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _json_error(
            exc.code,
            status=exc.spec.http_status,
            message=exc.message,
            details=exc.details,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details, workspace_id_present = _validation_details(exc)
        return _json_error(
            ErrorCode.VALIDATION_ERROR,
            details={"reason": WORKSPACE_ID_NOT_ALLOWED} if workspace_id_present else details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapping = {
            401: ErrorCode.AUTH_SESSION_EXPIRED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.METHOD_NOT_ALLOWED,
            413: ErrorCode.PAYLOAD_TOO_LARGE,
            415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            429: ErrorCode.RATE_LIMITED,
        }
        code = mapping.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return _json_error(code, status=exc.status_code, details=None)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # SEC-19：响应与日志都不得包含堆栈、SQL 或参数（可能含密码哈希/令牌）。
        # 需要完整堆栈时显式设置 MYLIFE_LOG_EXCEPTIONS=true（仅限受控调试）。
        from mylife_api.settings import settings as _settings

        if _settings.log_exceptions:
            logger.exception("unhandled error", extra={"request_id": current_request_id()})
        else:
            logger.error(
                "unhandled error",
                extra={
                    "request_id": current_request_id(),
                    "error_type": type(exc).__name__,
                    "event": "unhandled_error",
                },
            )
        return _json_error(ErrorCode.INTERNAL_ERROR, headers={"Retry-After": "3"})
