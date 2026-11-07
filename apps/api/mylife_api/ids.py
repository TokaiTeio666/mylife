"""ID 生成：UUIDv7、ULID、request_id、token。"""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import UTC, datetime

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def uuid7() -> uuid.UUID:
    """生成 UUIDv7（时间有序）。

    Python 3.14 起标准库提供 `uuid.uuid7`；旧版本用等价实现。
    """
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


def ulid(now: datetime | None = None) -> str:
    """ULID：48 位毫秒时间戳 + 80 位随机，Crockford Base32。"""
    if now is None:
        now = datetime.now(UTC)
    millis = int(now.timestamp() * 1000)
    randomness = secrets.randbits(80)
    value = (millis << 80) | randomness
    chars = ["0"] * 26
    for i in range(25, -1, -1):
        chars[i] = _CROCKFORD[value & 31]
        value >>= 5
    return "".join(chars)


def request_id() -> str:
    """`req_` + ULID（API-CONTRACTS §1.5）。"""
    return f"req_{ulid()}"


def session_token() -> str:
    """32 字节 base64url 不透明 token（§1.3）。"""
    return secrets.token_urlsafe(32)


def csrf_token() -> str:
    return secrets.token_urlsafe(32)


def invite_code() -> str:
    """8–32 字符邀请码；使用无歧义大写字母表。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    body = "".join(secrets.choice(alphabet) for _ in range(10))
    return f"INV-{body}"


def short_slug_suffix() -> str:
    return secrets.token_hex(4)
