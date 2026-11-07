"""原子写（DIRECTORY-CONSTRAINTS §7.1）。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from mylife_runtime.paths import PathRejected, is_within


def atomic_write_bytes(
    target: Path,
    data: bytes,
    *,
    root: Path | None = None,
    tmp_dir: Path | None = None,
) -> None:
    """同分区临时文件 → fsync → rename；失败不留半文件。"""
    target = Path(target)
    if root is not None and not is_within(root, target):
        raise PathRejected("ESCAPES_ROOT", str(target))

    target.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tmp_dir) if tmp_dir is not None else target.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    tmp_name = f".{target.name}.{secrets.token_hex(8)}.tmp"
    tmp_path = work_dir / tmp_name
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def atomic_write_text(
    target: Path, text: str, *, root: Path | None = None, tmp_dir: Path | None = None
) -> None:
    atomic_write_bytes(target, text.encode("utf-8"), root=root, tmp_dir=tmp_dir)
