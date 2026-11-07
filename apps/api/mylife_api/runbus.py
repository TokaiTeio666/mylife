"""进程内 SSE 事件总线（仅承载不持久化的 token/heartbeat）。

持久化事件仍以 `run_events` 为准；同一 API 进程内的 SSE 订阅者可以更低延迟
收到 token 增量。跨进程部署时 token 不保证送达，客户端以最终消息为准。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID


class RunBus:
    def __init__(self) -> None:
        self._subscribers: dict[UUID, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    @asynccontextmanager
    async def subscribe(self, run_id: UUID) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        self._subscribers[run_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                self._subscribers.pop(run_id, None)

    async def publish(self, run_id: UUID, frame: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(run_id, ())):
            # 慢消费者丢弃增量；最终消息与持久化事件仍在 DB。
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(frame)


bus = RunBus()
