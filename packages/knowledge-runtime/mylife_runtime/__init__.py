"""mylife 知识运行时：路径校验、原子写、凭据信封加密。

本包不依赖任何 app、数据库或框架（DIRECTORY-CONSTRAINTS §2.1）。
"""

from mylife_runtime.atomic import atomic_write_bytes, atomic_write_text
from mylife_runtime.crypto import (
    CredentialDecryptError,
    decrypt_credential,
    encrypt_credential,
)
from mylife_runtime.paths import (
    PathRejected,
    check_no_symlink,
    is_within,
    normalize_nfc,
    safe_join,
    validate_relative_path,
    validate_vault_path,
)

__all__ = [
    "CredentialDecryptError",
    "PathRejected",
    "atomic_write_bytes",
    "atomic_write_text",
    "check_no_symlink",
    "decrypt_credential",
    "encrypt_credential",
    "is_within",
    "normalize_nfc",
    "safe_join",
    "validate_relative_path",
    "validate_vault_path",
]
