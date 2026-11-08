"""LLM 设置与连接测试（API-CONTRACTS §3.9.1–§3.9.3；AC-49 / FR-G1）。

关键约束：
- 明文 API Key 只存在于内存，落库为信封密文（AAD = `workspace_id|llm`）；
- `PUT` 默认“先测试、成功才落库”，失败时 `provider_credentials` 行数不变；
- 错误与日志都不回显密钥（SEC-19）；
- 运行时读取顺序：DB 行 → 环境变量 → mock（PHASE5-SPEC D7）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from mylife_agent.llm import (
    LLMKeyMissing,
    LLMProvider,
    LLMUnavailable,
    build_llm_provider,
)
from mylife_contract.errors import ErrorCode
from mylife_contract.settings import (
    LlmSettings,
    LlmTestResult,
    LlmUpdate,
    ProviderTestError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories
from mylife_api.crypto import decrypt_credential, encrypt_credential
from mylife_api.errors import AppError
from mylife_api.repositories import insert_audit
from mylife_api.settings import settings as app_settings

logger = logging.getLogger("mylife.api.llm")

#: 连接测试超时（API §3.9.3）。
TEST_TIMEOUT_SECONDS = 15.0

#: wire provider → `build_llm_provider` 认识的名字（PHASE5-SPEC 风险 9）。
WIRE_TO_BUILDER = {
    "deepseek": "deepseek",
    "openai_compatible": "openai",
    "mock": "mock",
}
#: DB 状态 → wire 状态（PHASE5-SPEC D6）。
DB_TO_WIRE_STATUS = {
    "active": "valid",
    "invalid": "invalid",
    "disabled": "missing",
    "untested": "untested",
}


def _wire_status(row: dict[str, Any] | None) -> str:
    if row is None:
        return "missing"
    return DB_TO_WIRE_STATUS.get(str(row.get("status") or ""), "untested")


def _validate_base_url(value: str) -> None:
    """只允许 https；`http://` 仅限白名单内的私有主机（PHASE5-SPEC D26）。"""
    parsed = urlparse(value)
    if parsed.username or parsed.password:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details={"field": "base_url", "reason": "CREDENTIAL_IN_URL"},
        )
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details={"field": "base_url", "reason": "SSRF_BLOCKED"},
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details={"field": "base_url", "reason": "HOST_MISSING"},
        )
    if scheme == "https":
        return
    allowed = {
        item.strip().lower()
        for item in (app_settings.allowed_private_provider_hosts or "").split(",")
        if item.strip()
    }
    if host in allowed:
        return
    # 非白名单的 http 一律拒绝：模型密钥不能走明文链路。
    raise AppError(
        ErrorCode.VALIDATION_ERROR,
        details={"field": "base_url", "reason": "HTTPS_REQUIRED"},
    )


def _provider_for(*, provider: str, base_url: str, api_key: str, model: str) -> LLMProvider:
    builder = WIRE_TO_BUILDER.get(provider)
    if builder is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details={"field": "provider", "reason": "UNSUPPORTED"},
        )
    try:
        return build_llm_provider(
            provider=builder, base_url=base_url, api_key=api_key, model=model
        )
    except LLMKeyMissing as exc:
        raise AppError(ErrorCode.PROVIDER_KEY_MISSING) from exc


async def _probe(
    *, provider: str, base_url: str, api_key: str, model: str
) -> tuple[bool, int, str | None, ProviderTestError | None]:
    """发起一次最小请求；返回 `(ok, latency_ms, model_echo, error)`。

    不写 `usage_events`、不碰数据库。`mock` 直接成功，便于离线验收。
    """
    started = time.perf_counter()
    if provider == "mock":
        return True, 0, model, None
    try:
        provider_impl = _provider_for(
            provider=provider, base_url=base_url, api_key=api_key, model=model
        )
    except AppError as exc:
        return False, 0, None, ProviderTestError(code=exc.code.value, message=exc.message)

    # 复用真实 provider 的一次极短流式调用；任何异常都只返回脱敏后的错误码。
    from mylife_agent.llm import LLMRequest

    try:
        async def _drain() -> None:
            async for _ in provider_impl.stream(
                LLMRequest(question="ping", evidence=[], max_tokens=8)
            ):
                pass

        await asyncio.wait_for(_drain(), timeout=TEST_TIMEOUT_SECONDS)
    except TimeoutError:
        return (
            False,
            int((time.perf_counter() - started) * 1000),
            None,
            ProviderTestError(code="LLM_UNAVAILABLE", message="连接超时"),
        )
    except LLMUnavailable:
        return (
            False,
            int((time.perf_counter() - started) * 1000),
            None,
            ProviderTestError(code="LLM_UNAVAILABLE", message="上游模型不可用"),
        )
    except Exception:  # noqa: BLE001 - 测试端点必须给出可读错误而非 500
        logger.warning("llm test failed provider=%s", provider)
        return (
            False,
            int((time.perf_counter() - started) * 1000),
            None,
            ProviderTestError(code="LLM_UNAVAILABLE", message="连接测试失败"),
        )
    latency = int((time.perf_counter() - started) * 1000)
    return True, latency, model, None


async def get_llm_settings(session: AsyncSession, *, workspace_id: UUID) -> LlmSettings:
    row = await repositories.get_provider_credential(
        session, workspace_id=workspace_id, provider="llm"
    )
    if row is None:
        # 无 DB 行时回落到环境变量；仍未配置则为 null + missing。
        env_provider = app_settings.llm_provider
        if env_provider in {"mock", None, ""}:
            return LlmSettings(
                provider="mock" if env_provider == "mock" else None,
                base_url=app_settings.llm_base_url,
                model=app_settings.llm_model if env_provider != "mock" else None,
                credential_status="missing" if env_provider != "mock" else "valid",
            )
        return LlmSettings(
            provider="deepseek" if env_provider == "deepseek" else "openai_compatible",
            base_url=app_settings.llm_base_url,
            model=app_settings.llm_model,
            credential_status="valid" if app_settings.llm_api_key else "missing",
        )
    return LlmSettings(
        provider=_wire_provider(row),
        base_url=row.get("base_url"),
        model=row.get("model"),
        model_profile=row.get("model_profile") or "default",
        credential_status=_wire_status(row),  # type: ignore[arg-type]
        last_tested_at=row.get("last_tested_at"),
    )


def _wire_provider(row: dict[str, Any]) -> str:
    """DB 的 `provider_name` 即 wire 值；历史行按 model_profile 推断。"""
    name = row.get("provider_name")
    if name in {"deepseek", "openai_compatible", "mock"}:
        return str(name)
    profile = str(row.get("model_profile") or "default")
    if profile == "mock":
        return "mock"
    if profile == "openai_compatible":
        return "openai_compatible"
    return "deepseek"


async def test_llm(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    provider: str,
    base_url: str,
    model: str,
    api_key: str | None,
) -> LlmTestResult:
    """不落库的连接测试（API §3.9.3）。"""
    _validate_base_url(base_url)
    key = api_key
    if key is None:
        row = await repositories.get_provider_credential(
            session, workspace_id=workspace_id, provider="llm"
        )
        if row is not None:
            key = decrypt_credential(
                bytes(row["key_ciphertext"]), workspace_id=str(workspace_id), provider="llm"
            )
    if provider != "mock" and not key:
        raise AppError(ErrorCode.PROVIDER_KEY_MISSING)
    ok, latency, echo, error = await _probe(
        provider=provider, base_url=base_url, api_key=key or "", model=model
    )
    return LlmTestResult(ok=ok, latency_ms=latency, model_echo=echo, error=error)


async def put_llm_settings(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
    payload: LlmUpdate,
) -> LlmSettings:
    """先测试、成功才落库（AC-49）。"""
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    _validate_base_url(payload.base_url)

    existing = await repositories.get_provider_credential(
        session, workspace_id=workspace_id, provider="llm"
    )
    key = payload.api_key
    if key is None and existing is not None:
        key = decrypt_credential(
            bytes(existing["key_ciphertext"]), workspace_id=str(workspace_id), provider="llm"
        )
    if payload.provider != "mock" and not key:
        raise AppError(ErrorCode.PROVIDER_KEY_MISSING)

    if payload.test_before_save:
        ok, _latency, _echo, error = await _probe(
            provider=payload.provider,
            base_url=payload.base_url,
            api_key=key or "",
            model=payload.model,
        )
        if not ok:
            # 失败不落库：行数与内容保持原样（AC-49）。
            raise AppError(
                ErrorCode.LLM_UNAVAILABLE,
                details={
                    "code": error.code if error else "LLM_UNAVAILABLE",
                    "message": error.message if error else "连接测试失败",
                },
            )

    if payload.api_key is not None or existing is None:
        await repositories.upsert_provider_credential(
            session,
            workspace_id=workspace_id,
            provider="llm",
            key_ciphertext=encrypt_credential(
                key or "", workspace_id=str(workspace_id), provider="llm"
            ),
            key_version=1,
            base_url=payload.base_url,
            model=payload.model,
            model_profile="default",
            provider_name=payload.provider,
            status="active",
        )
    else:
        await repositories.update_provider_credential_meta(
            session,
            workspace_id=workspace_id,
            provider="llm",
            status="active",
            base_url=payload.base_url,
            model=payload.model,
            model_profile="default",
            provider_name=payload.provider,
        )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="settings.llm.updated",
        target=str(workspace_id),
        # 只记 provider/model/主机；绝不记 key（SEC-19）。
        detail={
            "provider": payload.provider,
            "model": payload.model,
            "host": urlparse(payload.base_url).hostname,
        },
    )
    return await get_llm_settings(session, workspace_id=workspace_id)


async def build_llm_for_workspace(
    session: AsyncSession, *, workspace_id: UUID
) -> LLMProvider:
    """运行时构建：DB 行 → 环境变量 → mock（PHASE5-SPEC D7）。"""
    row = await repositories.get_provider_credential(
        session, workspace_id=workspace_id, provider="llm"
    )
    if row is not None:
        provider = _wire_provider(row)
        if provider == "mock":
            return build_llm_provider(
                provider="mock", base_url=None, api_key=None, model="mock-chat-v1"
            )
        api_key = decrypt_credential(
            bytes(row["key_ciphertext"]), workspace_id=str(workspace_id), provider="llm"
        )
        return _provider_for(
            provider=provider,
            base_url=str(row.get("base_url") or app_settings.llm_base_url or ""),
            api_key=api_key,
            model=str(row.get("model") or app_settings.llm_model),
        )
    if app_settings.llm_provider == "mock":
        return build_llm_provider(
            provider="mock", base_url=None, api_key=None, model="mock-chat-v1"
        )
    return build_llm_provider(
        provider=WIRE_TO_BUILDER.get(app_settings.llm_provider, "openai"),
        base_url=app_settings.llm_base_url,
        api_key=app_settings.llm_api_key,
        model=app_settings.llm_model,
    )


__all__ = [
    "build_llm_for_workspace",
    "get_llm_settings",
    "put_llm_settings",
    "test_llm",
]
