"""忽略规则（DIRECTORY-CONSTRAINTS §6.1 / FR-B3）。

默认规则不可删除，只能追加。
"""

from __future__ import annotations

import fnmatch

DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {".obsidian", ".trash", ".stfolder", ".git", "node_modules"}
)
DEFAULT_IGNORE_FILES: frozenset[str] = frozenset({".DS_Store", "Thumbs.db"})
DEFAULT_IGNORE_GLOBS: tuple[str, ...] = ("*.tmp", "*.swp")

MARKDOWN_EXTENSIONS = {".md", ".markdown"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


class IgnoreMatcher:
    def __init__(
        self,
        extra_dirs: frozenset[str] | set[str] | None = None,
        extra_globs: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        # 默认规则不可删除
        self.dirs = DEFAULT_IGNORE_DIRS | frozenset(extra_dirs or ())
        self.globs = DEFAULT_IGNORE_GLOBS + tuple(extra_globs or ())

    def is_ignored(self, path: str) -> bool:
        parts = path.split("/")
        if any(part in self.dirs for part in parts[:-1]):
            return True
        if parts and parts[-1] in self.dirs:
            return True
        if parts and parts[-1] in DEFAULT_IGNORE_FILES:
            return True
        return any(fnmatch.fnmatch(parts[-1], pattern) for pattern in self.globs)


def is_markdown(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in MARKDOWN_EXTENSIONS)


def is_image(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in IMAGE_EXTENSIONS)


def extension_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()
