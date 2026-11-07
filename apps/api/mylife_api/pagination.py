"""游标分页（API-CONTRACTS §1.6）。

游标为 base64url 编码的 `(period_key, version)`；非法游标返回 `CURSOR_INVALID`。
"""

from __future__ import annotations

import base64
import json

from mylife_contract.errors import ErrorCode

from mylife_api.errors import AppError


def encode_review_cursor(period_key: str, version: int) -> str:
    payload = json.dumps({"p": period_key, "v": version}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_review_cursor(cursor: str | None) -> tuple[str, int] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        payload = json.loads(raw)
        return str(payload["p"]), int(payload["v"])
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都视为非法游标
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"reason": "CURSOR_INVALID"}) from exc
