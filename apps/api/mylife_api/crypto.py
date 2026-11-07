"""凭据信封加密的 API 适配层。

密钥派生与加解密实现在 `mylife_runtime.crypto`（worker 共用）；
本模块只负责注入部署级 `MYLIFE_SECRET_KEY`。
"""

from __future__ import annotations

from mylife_runtime.crypto import (
    KEY_VERSION,
    CredentialDecryptError,
)
from mylife_runtime.crypto import (
    decrypt_credential as _decrypt,
)
from mylife_runtime.crypto import (
    encrypt_credential as _encrypt,
)

from mylife_api.settings import settings

__all__ = ["CredentialDecryptError", "decrypt_credential", "encrypt_credential"]


def encrypt_credential(
    plaintext: str,
    *,
    workspace_id: str,
    provider: str,
    key_version: int = KEY_VERSION,
) -> bytes:
    return _encrypt(
        plaintext,
        secret_key=settings.secret_key,
        workspace_id=workspace_id,
        provider=provider,
        key_version=key_version,
    )


def decrypt_credential(blob: bytes, *, workspace_id: str, provider: str) -> str:
    return _decrypt(
        blob,
        secret_key=settings.secret_key,
        workspace_id=workspace_id,
        provider=provider,
    )
