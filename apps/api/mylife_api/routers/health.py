"""健康检查（API-CONTRACTS §1.1）：/healthz 存活、/readyz 就绪。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from mylife_api.db import get_engine
from mylife_api.redis_probe import probe_redis

logger = logging.getLogger("mylife.api.health")

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(response: Response) -> dict[str, object]:
    checks: dict[str, str] = {}
    ready = True

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:  # noqa: BLE001 - 就绪探针需要报告任意失败
        checks["db"] = "unavailable"
        ready = False

    redis_status = await probe_redis()
    checks["redis"] = redis_status
    # Redis 是队列/限流依赖，不是事实源；不可用时 readyz 仍为降级而非未就绪。
    if redis_status not in ("ok", "disabled", "memory"):
        checks["redis"] = "degraded"

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}
