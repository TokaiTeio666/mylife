"""读取冻结快照原文（ARCHITECTURE §9.1 G3c）。

Reviewer 只对冻结原文复算引用；Git 源取 snapshot commit 的 blob，ZIP 源取
快照文档副本。Phase 3 的快照目录由 ingest 写入 `{storage_path}/{relative_path}`。
"""

from __future__ import annotations

from pathlib import Path

from mylife_runtime.paths import PathRejected, check_no_symlink, safe_join


def load_frozen_text(storage_path: str | Path, relative_path: str) -> str | None:
    """按快照目录 + 相对路径读取原文；路径越界/符号链接一律拒绝。

    读取失败返回 None（由调用方决定降级或 blocking）。
    """
    if not storage_path:
        return None
    root = Path(storage_path)
    try:
        target = safe_join(root, relative_path, vault=True)
    except PathRejected:
        return None
    try:
        check_no_symlink(target)
        data = target.read_bytes()
    except (OSError, PathRejected):
        return None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None
