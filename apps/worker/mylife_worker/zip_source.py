"""ZIP 导入源（FR-B2；DIRECTORY-CONSTRAINTS §6.3；SEC-13/14）。

安全约束：压缩 ≤200MB、解压 ≤2GB、单文件 ≤20MB、文件数 ≤50k、压缩比 ≤100:1、
拒绝路径穿越/绝对路径/符号链接、不递归解压嵌套 ZIP。
"""

from __future__ import annotations

import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mylife_runtime.paths import PathRejected, safe_join

from mylife_worker.ignore import extension_of, is_image, is_markdown


class ZipRejected(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        self.message = message
        super().__init__(message)


@dataclass(slots=True)
class ZipLimits:
    max_compressed_bytes: int = 200 * 1024 * 1024
    max_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024
    max_file_bytes: int = 20 * 1024 * 1024
    max_files: int = 50_000
    max_ratio: int = 100


@dataclass(slots=True)
class ExtractedFile:
    path: str
    size: int
    mtime: datetime | None


@dataclass(slots=True)
class ExtractionResult:
    files: list[ExtractedFile] = field(default_factory=list)
    skipped: int = 0
    total_uncompressed: int = 0


def _entry_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0xFFFF


def _validate_entry_name(name: str) -> str:
    if "\x00" in name:
        raise ZipRejected("INGEST_ZIP_INVALID", "条目包含 NUL")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise ZipRejected("INGEST_ZIP_INVALID", f"绝对路径条目: {name}")
    segments = normalized.rstrip("/").split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ZipRejected("INGEST_ZIP_INVALID", f"路径穿越条目: {name}")
    return "/".join(segments)


def inspect_zip(zip_path: Path, limits: ZipLimits | None = None) -> list[zipfile.ZipInfo]:
    limits = limits or ZipLimits()
    try:
        compressed = zip_path.stat().st_size
    except OSError as exc:
        raise ZipRejected("INGEST_ZIP_INVALID", "无法读取 ZIP") from exc
    if compressed > limits.max_compressed_bytes:
        raise ZipRejected("INGEST_ZIP_LIMIT", "压缩包超过 200MB")

    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > limits.max_files:
                raise ZipRejected("INGEST_ZIP_LIMIT", "文件数超过 50000")
            total_uncompressed = 0
            total_compressed = 0
            for info in infos:
                _validate_entry_name(info.filename)
                mode = _entry_mode(info)
                if stat.S_ISLNK(mode):
                    raise ZipRejected("INGEST_ZIP_INVALID", f"符号链接条目: {info.filename}")
                if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
                    raise ZipRejected("INGEST_ZIP_INVALID", f"设备文件条目: {info.filename}")
                if info.flag_bits & 0x1:
                    raise ZipRejected("INGEST_ZIP_INVALID", f"加密条目不支持: {info.filename}")
                total_uncompressed += info.file_size
                total_compressed += info.compress_size
                if info.file_size > limits.max_file_bytes:
                    raise ZipRejected("INGEST_ZIP_LIMIT", f"单文件超过 20MB: {info.filename}")
            if total_uncompressed > limits.max_uncompressed_bytes:
                raise ZipRejected("INGEST_ZIP_LIMIT", "解压后超过 2GB")
            if total_compressed > 0 and total_uncompressed / total_compressed > limits.max_ratio:
                raise ZipRejected("INGEST_ZIP_LIMIT", "压缩比超过 100:1")
            return infos
    except zipfile.BadZipFile as exc:
        raise ZipRejected("INGEST_ZIP_INVALID", "ZIP 损坏或不是 ZIP") from exc


def extract_zip(
    zip_path: Path, destination: Path, limits: ZipLimits | None = None
) -> ExtractionResult:
    limits = limits or ZipLimits()
    infos = inspect_zip(zip_path, limits)
    destination.mkdir(parents=True, exist_ok=True)
    result = ExtractionResult()

    with zipfile.ZipFile(zip_path) as archive:
        for info in infos:
            name = _validate_entry_name(info.filename)
            extension = extension_of(name)
            if not (is_markdown(name) or is_image(name)):
                result.skipped += 1
                continue
            if extension == "zip":
                result.skipped += 1
                continue
            try:
                target = safe_join(destination, name, vault=True)
            except PathRejected as exc:
                raise ZipRejected(
                    "INGEST_ZIP_INVALID", f"路径被拒绝: {name} ({exc.reason})"
                ) from exc

            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info, "r") as source, open(target, "wb") as sink:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limits.max_file_bytes:
                        sink.close()
                        shutil.rmtree(destination, ignore_errors=True)
                        raise ZipRejected(
                            "INGEST_ZIP_LIMIT", f"解压时单文件超过 20MB: {name}"
                        )
                    if result.total_uncompressed + written > limits.max_uncompressed_bytes:
                        sink.close()
                        shutil.rmtree(destination, ignore_errors=True)
                        raise ZipRejected("INGEST_ZIP_LIMIT", "解压时超过 2GB 上限")
                    sink.write(chunk)
            result.total_uncompressed += written
            try:
                mtime = datetime(*info.date_time)
            except (TypeError, ValueError):
                mtime = None
            result.files.append(ExtractedFile(path=name, size=written, mtime=mtime))
    return result
