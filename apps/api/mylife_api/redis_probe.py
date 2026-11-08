"""Redis 探针。"""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis

from mylife_api.settings import settings

#: 探针超时：Redis 被暂停/断网时 ping 会一直挂起，`/readyz` 必须能按时返回
#: “降级”，而不是把整个健康检查拖死（AC-59 的 30s 断网场景）。
PROBE_TIMEOUT_SECONDS = 2.0


async def probe_redis() -> str:
    if settings.rate_limit_backend == "off":
        return "disabled"
    if settings.rate_limit_backend == "memory":
        return "memory"
    client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
        socket_timeout=PROBE_TIMEOUT_SECONDS,
    )
    try:
        await asyncio.wait_for(client.ping(), timeout=PROBE_TIMEOUT_SECONDS)
        return "ok"
    except Exception:  # noqa: BLE001 - 探针需要报告任意失败（含超时）
        return "unavailable"
    finally:
        await client.aclose()
