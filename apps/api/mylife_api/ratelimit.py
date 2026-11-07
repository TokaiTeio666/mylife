"""限流与登录锁定（API-CONTRACTS §8）。

实现：Redis 固定窗口（INCR + EXPIRE）；开发/测试可在内存中降级。
Redis 不可用时：读保守放行、写拒绝（由调用方决定），并记录 `ratelimit_degraded`。
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from mylife_api.settings import settings

logger = logging.getLogger("mylife.api.ratelimit")


@dataclass(slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int  # Unix 秒
    degraded: bool = False


class RateLimiter:
    def __init__(self, redis_url: str | None = None) -> None:
        self._memory: dict[str, tuple[int, float]] = {}
        self._redis: Redis | None = None
        if settings.rate_limit_backend == "redis":
            self._redis = Redis.from_url(redis_url or settings.redis_url, decode_responses=True)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = int(time.time())
        reset_at = now + window_seconds
        if settings.rate_limit_backend == "off":
            return RateLimitResult(True, limit, limit, reset_at)
        if self._redis is not None:
            try:
                count = await self._redis.incr(key)
                if count == 1:
                    await self._redis.expire(key, window_seconds)
                ttl = await self._redis.ttl(key)
                reset_at = now + (ttl if ttl and ttl > 0 else window_seconds)
                return RateLimitResult(
                    allowed=count <= limit,
                    limit=limit,
                    remaining=max(0, limit - count),
                    reset_at=reset_at,
                )
            except RedisError:
                logger.warning("ratelimit_degraded", extra={"event": "ratelimit_degraded"})
                if settings.is_production:
                    # 生产：Redis 不可用时保守放行读、拒绝写由调用方处理；
                    # 登录属于写，拒绝。
                    return RateLimitResult(False, limit, 0, reset_at, degraded=True)
        return self._memory_hit(key, limit, window_seconds, now)

    def _memory_hit(self, key: str, limit: int, window_seconds: int, now: int) -> RateLimitResult:
        count, expires_at = self._memory.get(key, (0, now + window_seconds))
        if now >= expires_at:
            count, expires_at = 0, now + window_seconds
        count += 1
        self._memory[key] = (count, expires_at)
        return RateLimitResult(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            reset_at=int(expires_at),
        )

    async def reset(self, key: str) -> None:
        self._memory.pop(key, None)
        if self._redis is not None:
            with contextlib.suppress(RedisError):
                await self._redis.delete(key)


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
