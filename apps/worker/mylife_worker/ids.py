"""ID 生成（worker 侧；与 API 保持同样的 UUIDv7 语义）。"""

from __future__ import annotations

import secrets
import time
import uuid


def uuid7() -> uuid.UUID:
    factory = getattr(uuid, "uuid7", None)
    if factory is not None:
        return factory()
    millis = int(time.time() * 1000)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (millis & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)
