"""路径校验（DIRECTORY-CONSTRAINTS §4.1 / §7.4 / §11）。

拒绝一律抛 `PathRejected`，调用方转 `PATH_REJECTED` / 404，不重试。
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

# 中文、字母、数字、空格、- _ . ( ) （ ）
_ALLOWED_CHARS = re.compile(r"^[\w一-鿿 .()（）\-]+$", re.UNICODE)
_FORBIDDEN_CHARS = re.compile(r'[<>:"?*|\\\x00-\x1f]')
_DRIVE = re.compile(r"^[A-Za-z]:")


class PathRejected(ValueError):
    """路径不满足白名单约束。"""

    def __init__(self, reason: str, path: str) -> None:
        self.reason = reason
        self.path = path
        super().__init__(f"{reason}: {path!r}")


def normalize_nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _check_common(path: str) -> str:
    if not isinstance(path, str) or path == "":
        raise PathRejected("EMPTY_PATH", path)
    if "\x00" in path:
        raise PathRejected("NUL_BYTE", path)
    if path.startswith("file://"):
        raise PathRejected("FILE_URL", path)
    if path.startswith(("/", "\\")):
        raise PathRejected("ABSOLUTE_PATH", path)
    if _DRIVE.match(path):
        raise PathRejected("DRIVE_PATH", path)
    if path.startswith("\\\\"):
        raise PathRejected("UNC_PATH", path)
    if "\\" in path:
        raise PathRejected("BACKSLASH", path)
    if path.endswith("/"):
        raise PathRejected("TRAILING_SLASH", path)
    if "//" in path:
        raise PathRejected("EMPTY_SEGMENT", path)
    return normalize_nfc(path)


def validate_relative_path(
    path: str,
    *,
    max_depth: int = 8,
    max_length: int = 240,
    max_segment: int = 120,
    allow_dot_metadata: bool = False,
) -> str:
    """校验 `reviews_root` 下的生成物路径，返回 NFC 规范化结果。"""
    normalized = _check_common(path)
    if len(normalized) > max_length:
        raise PathRejected("PATH_TOO_LONG", normalized)

    segments = normalized.split("/")
    if len(segments) > max_depth:
        raise PathRejected("DEPTH_EXCEEDED", normalized)

    for segment in segments:
        if segment in ("", ".", ".."):
            raise PathRejected("FORBIDDEN_SEGMENT", normalized)
        if segment.startswith(".") and not (allow_dot_metadata and segment == ".mylife"):
            raise PathRejected("HIDDEN_SEGMENT", normalized)
        if len(segment) > max_segment:
            raise PathRejected("SEGMENT_TOO_LONG", normalized)
        if _FORBIDDEN_CHARS.search(segment):
            raise PathRejected("FORBIDDEN_CHAR", normalized)
        if not _ALLOWED_CHARS.match(segment):
            raise PathRejected("CHAR_NOT_ALLOWED", normalized)
    return normalized


def validate_vault_path(path: str, *, max_length: int = 1024) -> str:
    """校验来自 Vault 的相对路径（允许更深层目录，但仍禁止穿越与隐藏元数据目录）。"""
    normalized = _check_common(path)
    if len(normalized) > max_length:
        raise PathRejected("PATH_TOO_LONG", normalized)
    for segment in normalized.split("/"):
        if segment in ("", ".", ".."):
            raise PathRejected("FORBIDDEN_SEGMENT", normalized)
        if _FORBIDDEN_CHARS.search(segment):
            raise PathRejected("FORBIDDEN_CHAR", normalized)
    return normalized


def is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (ValueError, OSError):
        return False


def safe_join(root: Path, relative: str, *, vault: bool = False) -> Path:
    """把相对路径拼到 root 下，并再次确认不越界。"""
    normalized = validate_vault_path(relative) if vault else validate_relative_path(relative)
    target = root / normalized
    if not is_within(root, target):
        raise PathRejected("ESCAPES_ROOT", normalized)
    return target


def check_no_symlink(path: Path) -> None:
    """拒绝符号链接与硬链接（硬链接在多数平台无法用 lstat 可靠识别，按 nlink>1 处理）。"""
    if path.is_symlink():
        raise PathRejected("SYMLINK", str(path))
    try:
        stat = os.lstat(path)
    except FileNotFoundError:
        return
    if getattr(stat, "st_nlink", 1) > 1:
        raise PathRejected("HARD_LINK", str(path))
