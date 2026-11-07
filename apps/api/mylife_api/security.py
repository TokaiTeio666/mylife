"""密码、会话令牌与 CSRF。

规范：Argon2id 密码哈希；session token 只存 SHA-256；CSRF 使用 double-submit
（非 HttpOnly Cookie + `X-CSRF-Token` 头），适用所有非安全方法（API-CONTRACTS §1.3）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from mylife_contract.constants import COOKIE_CSRF, COOKIE_SESSION

from mylife_api.settings import settings

# OWASP 推荐的 Argon2id 基线参数。
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:  # pragma: no cover - 防御性：损坏哈希一律视为不匹配
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


def hash_session_token(token: str) -> str:
    """服务端只存 token 的 SHA-256（§1.3）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)


def utcnow() -> datetime:
    return datetime.now(UTC)


def session_expiry(now: datetime | None = None) -> datetime:
    return (now or utcnow()) + timedelta(days=settings.session_ttl_days)


def session_cookie_kwargs(max_age: int | None = None) -> dict[str, object]:
    """HttpOnly 会话 Cookie（§1.3）：Secure; HttpOnly; SameSite=Lax; Path=/。"""
    return {
        "key": COOKIE_SESSION,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": max_age,
    }


def csrf_cookie_kwargs(max_age: int | None = None) -> dict[str, object]:
    """CSRF Cookie 必须可被前端 JS 读取，因此非 HttpOnly（§1.3）。"""
    return {
        "key": COOKIE_CSRF,
        "httponly": False,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": max_age,
    }


def clear_cookie_kwargs(key: str) -> dict[str, object]:
    return {
        "key": key,
        "value": "",
        "httponly": key == COOKIE_SESSION,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": 0,
    }
