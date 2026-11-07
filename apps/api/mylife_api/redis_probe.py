"""Redis 探针。"""

from __future__ import annotations

from redis.asyncio import Redis

from mylife_api.settings import settings


async def probe_redis() -> str:
    if settings.rate_limit_backend == "off":
        return "disabled"
    if settings.rate_limit_backend == "memory":
        return "memory"
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
        return "ok"
    except Exception:  # noqa: BLE001 - 探针需要报告任意失败
        return "unavailable"
    finally:
        await client.aclose()
