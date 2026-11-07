"""对象存储交付降级（ACK-39 等效证据；PHASE4-SPEC §4.4）。

本地数据根布局（DIRECTORY-CONSTRAINTS §3）：
`{data_root}/workspaces/{workspace_id}/artifacts/deliveries/{delivery_id}/`
含 `files/...`、`manifest.json` 与 `artifact.zip`。只有本模块写这些路径。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from mylife_delivery.git_ops import validate_relative_path


@dataclass(slots=True)
class ObjectBundle:
    delivery_id: str
    files_dir: Path
    manifest_path: Path
    zip_path: Path
    file_hashes: dict[str, str]


def bundle_root(data_root: Path, workspace_id: str, delivery_id: str) -> Path:
    return (
        Path(data_root)
        / "workspaces"
        / workspace_id
        / "artifacts"
        / "deliveries"
        / delivery_id
    )


def write_bundle(
    *,
    data_root: Path,
    workspace_id: str,
    delivery_id: str,
    files: dict[str, str],
    manifest: dict,
) -> ObjectBundle:
    base = bundle_root(data_root, workspace_id, delivery_id)
    files_dir = base / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    hashes: dict[str, str] = {}
    for path, content in files.items():
        validate_relative_path(path)
        target = files_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        hashes[path] = hashlib.sha256(content.encode("utf-8")).hexdigest()

    manifest_path = base / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    zip_path = base / "artifact.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(files_dir / path, arcname=path)
        archive.write(manifest_path, arcname="manifest.json")

    return ObjectBundle(
        delivery_id=delivery_id,
        files_dir=files_dir,
        manifest_path=manifest_path,
        zip_path=zip_path,
        file_hashes=hashes,
    )


def bundle_zip_path(data_root: Path, workspace_id: str, delivery_id: str) -> Path:
    return bundle_root(data_root, workspace_id, delivery_id) / "artifact.zip"


def read_manifest(data_root: Path, workspace_id: str, delivery_id: str) -> dict:
    path = bundle_root(data_root, workspace_id, delivery_id) / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "ObjectBundle",
    "bundle_root",
    "bundle_zip_path",
    "read_manifest",
    "write_bundle",
]
