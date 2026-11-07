"""凭据信封加密（SEC-18）。KEK 由调用方传入，包本身不读环境变量。

落库格式：`MYL1` + key_version(1B) + wrap_nonce(12B) + wrapped_dek(48B)
+ data_nonce(12B) + ciphertext；AAD 绑定 `workspace_id|provider|key_version`。
"""

from __future__ import annotations

import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_MAGIC = b"MYL1"
_NONCE = 12
_TAG = 16
_WRAPPED_DEK = 32 + _TAG
KEY_VERSION = 1


class CredentialDecryptError(Exception):
    """无法解密：KEK 不匹配或密文被篡改。不得向调用方暴露底层原因。"""


def _kek(secret_key: str) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"mylife-kek-v1",
        info=b"mylife/provider-credentials",
    )
    return hkdf.derive(secret_key.encode("utf-8"))


def _aad(workspace_id: str, provider: str, key_version: int) -> bytes:
    return f"{workspace_id}|{provider}|{key_version}".encode()


def encrypt_credential(
    plaintext: str,
    *,
    secret_key: str,
    workspace_id: str,
    provider: str,
    key_version: int = KEY_VERSION,
) -> bytes:
    if not plaintext:
        raise ValueError("credential must not be empty")
    if len(plaintext.encode("utf-8")) > 8192:
        raise ValueError("credential too large")

    aad = _aad(workspace_id, provider, key_version)
    dek = AESGCM.generate_key(bit_length=256)
    wrap_nonce = os.urandom(_NONCE)
    wrapped = AESGCM(_kek(secret_key)).encrypt(wrap_nonce, dek, aad)
    data_nonce = os.urandom(_NONCE)
    ciphertext = AESGCM(dek).encrypt(data_nonce, plaintext.encode("utf-8"), aad)
    return b"".join([_MAGIC, struct.pack("B", key_version), wrap_nonce, wrapped, data_nonce, ciphertext])


def decrypt_credential(
    blob: bytes, *, secret_key: str, workspace_id: str, provider: str
) -> str:
    try:
        if not blob.startswith(_MAGIC):
            raise CredentialDecryptError
        key_version = blob[len(_MAGIC)]
        offset = len(_MAGIC) + 1
        wrap_nonce = blob[offset : offset + _NONCE]
        offset += _NONCE
        wrapped = blob[offset : offset + _WRAPPED_DEK]
        offset += _WRAPPED_DEK
        data_nonce = blob[offset : offset + _NONCE]
        offset += _NONCE
        ciphertext = blob[offset:]

        aad = _aad(workspace_id, provider, key_version)
        dek = AESGCM(_kek(secret_key)).decrypt(wrap_nonce, wrapped, aad)
        return AESGCM(dek).decrypt(data_nonce, ciphertext, aad).decode("utf-8")
    except (InvalidTag, IndexError, ValueError, UnicodeDecodeError) as exc:
        raise CredentialDecryptError("credential cannot be decrypted") from exc
