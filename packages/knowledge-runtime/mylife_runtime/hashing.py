"""确定性哈希与归一化（DATA-MODEL §7.3；ARCHITECTURE §9.1 G3d）。

- `normalize_text`：NFC → 去首尾空白 → 连续空白折叠 → 中文标点统一；
  仅 key 类哈希（canonical_key / root_cause_fingerprint）做 ASCII 小写。
- `quote_hash`：`sha256(parser_version + "\\x1f" + normalize(quote))`。
- `content_hash`：`sha256(parser_version + "\\x1f" + normalized_body + "\\x1f" + canonical_frontmatter)`。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from mylife_contract.constants import PUNCT_NORMALIZATION

_WS = re.compile(r"\s+", re.UNICODE)
_ASCII_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)


def normalize_text(text: str, *, lowercase: bool = False) -> str:
    """NFC、去首尾空白、折叠空白、中文标点→半角；可选 ASCII 小写。"""
    value = unicodedata.normalize("NFC", text or "")
    value = _WS.sub(" ", value).strip()
    if PUNCT_NORMALIZATION:
        value = value.translate(str.maketrans(PUNCT_NORMALIZATION))
    if lowercase:
        value = value.translate(_ASCII_LOWER)
    return value


def sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def quote_hash(parser_version: str, quote: str) -> str:
    return sha256_hex(f"{parser_version}\x1f{normalize_text(quote)}")


def content_hash(parser_version: str, normalized_body: str, canonical_frontmatter: str) -> str:
    return sha256_hex(
        f"{parser_version}\x1f{normalized_body}\x1f{canonical_frontmatter}"
    )


def canonical_key(item_type: str, title: str) -> str:
    """`sha256(item_type + "\\x1f" + normalize(title | root_cause))`。"""
    return sha256_hex(f"{item_type}\x1f{normalize_text(title, lowercase=True)}")


def root_cause_fingerprint(root_cause: str) -> str:
    return sha256_hex(normalize_text(root_cause, lowercase=True))
