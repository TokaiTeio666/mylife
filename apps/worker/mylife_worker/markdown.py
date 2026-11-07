"""Markdown 解析与分块（ARCHITECTURE §2.1；AC-11/AC-17）。

- frontmatter 用 PyYAML `safe_load`，必须是映射；
- 标题按 ATX，跳过 fenced code；
- 分块按标题层级，`heading` 为标题路径（`A > B > C`）；
- 内容哈希 = sha256(parser_version + normalized_body + canonical_frontmatter)，路径不参与。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from mylife_agent.task_ledger import extract_occurrences
from mylife_retrieval.tokenizer import TOKENIZER_VERSION, build_search_text

PARSER_VERSION = f"mylife-parser-1:{TOKENIZER_VERSION}"

_FENCE = re.compile(r"^\s*(```|~~~)")
_ATX = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_WIKILINK = re.compile(r"(?<!!)\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
_IMAGE_LINK = re.compile(r"!\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
_FULL_DATE = re.compile(r"(\d{4})[-_.](\d{1,2})[-_.](\d{1,2})")
_SHORT_DATE = re.compile(r"^(\d{1,2})[.](\d{1,2})$")
_YEAR = re.compile(r"^(19|20)\d{2}$")


class ParseError(Exception):
    def __init__(self, reason_code: str, message: str, line: int | None = None) -> None:
        self.reason_code = reason_code
        self.message = message
        self.line = line
        super().__init__(message)


@dataclass(slots=True)
class ChunkData:
    heading: str | None
    content: str
    ordinal: int
    chunk_hash: str
    line_start: int
    line_end: int


@dataclass(slots=True)
class LinkData:
    target_path: str
    link_text: str | None


@dataclass(slots=True)
class TaskOccurrenceData:
    line_number: int
    checked: bool
    text: str


@dataclass(slots=True)
class ParsedDocument:
    path: str
    title: str
    body: str
    frontmatter: dict[str, Any]
    tags: list[str]
    occurred_on: date | None
    links: list[LinkData]
    chunks: list[ChunkData]
    content_hash: str
    search_text: str
    parser_version: str = PARSER_VERSION
    tokenizer_version: str = TOKENIZER_VERSION
    extra: dict[str, Any] = field(default_factory=dict)
    body_start_line: int = 1
    date_source: str | None = None
    parse_warnings: list[dict[str, Any]] = field(default_factory=list)
    task_occurrences: list[TaskOccurrenceData] = field(default_factory=list)


def normalize_body(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def json_default(value: Any) -> Any:
    """YAML 会把日期解析为 date/datetime；JSON 化时统一为 ISO 字符串。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def canonical_frontmatter(frontmatter: dict[str, Any]) -> str:
    return json.dumps(
        frontmatter,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )


def compute_content_hash(
    body: str, frontmatter: dict[str, Any], parser_version: str = PARSER_VERSION
) -> str:
    payload = f"{parser_version}\x1f{normalize_body(body)}\x1f{canonical_frontmatter(frontmatter)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_chunk_hash(heading: str | None, content: str) -> str:
    payload = f"{PARSER_VERSION}\x1f{heading or ''}\x1f{normalize_body(content)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, int]:
    """返回 (frontmatter, body, body_start_line_1based)。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---"):
        return {}, normalized, 1

    lines = normalized.split("\n")
    if lines[0].strip() != "---":
        return {}, normalized, 1

    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        raise ParseError("PARSE_ERROR", "frontmatter 缺少结束标记", 1)

    yaml_text = "\n".join(lines[1:end])
    try:
        loaded = yaml.safe_load(yaml_text) if yaml_text.strip() else {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise ParseError(
            "PARSE_ERROR",
            f"frontmatter YAML 非法: {getattr(exc, 'problem', str(exc))}",
            (mark.line + 2) if mark is not None else 2,
        ) from exc

    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ParseError("PARSE_ERROR", "frontmatter 必须是映射", 2)
    return loaded, "\n".join(lines[end + 1 :]), end + 2


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,，\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = [str(value)]
    tags: list[str] = []
    for item in raw:
        tag = item.strip().lstrip("#")
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _parse_date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text[: len(fmt) + 2], fmt).date()
            except ValueError:
                continue
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def derive_occurred_on(frontmatter: dict[str, Any], path: str) -> date | None:
    """兼容包装：只返回日期（AC-31/AC-34）。"""
    resolved, _warnings = resolve_occurred_on(frontmatter, path)
    return resolved


def resolve_occurred_on(
    frontmatter: dict[str, Any], path: str
) -> tuple[date | None, list[dict[str, Any]]]:
    """按优先级解析业务日期，并返回日期来源冲突/非法候选的 warning。

    优先级（AC-34 / PHASE3-SPEC §6.6）：
    frontmatter 显式日期 > 目录完整日期 > 文件名完整日期 > 文件名短日期 + 年份目录。
    不使用 mtime / ingest 时间。
    """
    warnings: list[dict[str, Any]] = []
    candidates: list[tuple[str, date]] = []

    for key in ("date", "occurred_on", "created", "created_at"):
        if key not in frontmatter:
            continue
        parsed = _parse_date_value(frontmatter[key])
        if parsed:
            candidates.append((f"frontmatter.{key}", parsed))
        else:
            warnings.append(
                {
                    "code": "DATE_INVALID",
                    "field": f"frontmatter.{key}",
                    "value": str(frontmatter.get(key))[:120],
                }
            )

    segments = path.split("/")
    directory_segments = segments[:-1]
    for segment in reversed(directory_segments):
        match = _FULL_DATE.fullmatch(segment)
        if not match:
            continue
        year, month, day = (int(part) for part in match.groups())
        try:
            candidates.append((f"directory:{segment}", date(year, month, day)))
        except ValueError:
            warnings.append({"code": "DATE_INVALID", "value": segment[:120]})
        break

    name = Path(path).stem
    full = _FULL_DATE.fullmatch(name)
    if full:
        year, month, day = (int(part) for part in full.groups())
        try:
            candidates.append((f"filename:{name}", date(year, month, day)))
        except ValueError:
            warnings.append({"code": "DATE_INVALID", "value": name[:120]})
    else:
        short = _SHORT_DATE.match(name)
        if short:
            year: int | None = None
            for segment in reversed(directory_segments):
                if _YEAR.match(segment):
                    year = int(segment)
                    break
                # 目录段是完整日期时，也提供年份（如 `日记/2026-10-28/10.27.md`）
                full_segment = _FULL_DATE.fullmatch(segment)
                if full_segment:
                    year = int(full_segment.group(1))
                    break
            if year is None:
                warnings.append({"code": "DATE_YEAR_MISSING", "path": path})
            else:
                try:
                    candidates.append(
                        (
                            f"filename:{name}",
                            date(year, int(short.group(1)), int(short.group(2))),
                        )
                    )
                except ValueError:
                    warnings.append({"code": "DATE_INVALID", "value": name[:120]})

    if not candidates:
        return None, warnings

    chosen_source, chosen_date = candidates[0]
    for source, value in candidates[1:]:
        if value != chosen_date:
            warnings.append(
                {
                    "code": "DATE_SOURCE_CONFLICT",
                    "chosen_source": chosen_source,
                    "chosen_date": chosen_date.isoformat(),
                    "ignored_source": source,
                    "ignored_date": value.isoformat(),
                }
            )
    return chosen_date, warnings


def _date_source(
    frontmatter: dict[str, Any], path: str, occurred_on: date | None
) -> str | None:
    """返回选中日期的来源标签（便于 AC-34 排障与 warning 对照）。"""
    if occurred_on is None:
        return None
    for key in ("date", "occurred_on", "created", "created_at"):
        if key in frontmatter and _parse_date_value(frontmatter[key]) == occurred_on:
            return f"frontmatter.{key}"
    segments = path.split("/")[:-1]
    for segment in reversed(segments):
        match = _FULL_DATE.fullmatch(segment)
        if match:
            year, month, day = (int(part) for part in match.groups())
            try:
                if date(year, month, day) == occurred_on:
                    return f"directory:{segment}"
            except ValueError:
                break
            break
    name = Path(path).stem
    if _FULL_DATE.fullmatch(name) or _SHORT_DATE.match(name):
        return f"filename:{name}"
    return None


def _iter_headings(body_lines: list[str]) -> list[tuple[int, int, str]]:
    """返回 (line_index_0based, level, text)，跳过 fenced code。"""
    headings: list[tuple[int, int, str]] = []
    fence: str | None = None
    for index, line in enumerate(body_lines):
        fence_match = _FENCE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        match = _ATX.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    return headings


def _build_chunks(
    body_lines: list[str], body_start_line: int
) -> tuple[list[ChunkData], list[str]]:
    headings = _iter_headings(body_lines)
    heading_at = {index: (level, text) for index, level, text in headings}

    chunks: list[ChunkData] = []
    h1_titles: list[str] = []
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    buffer_start = 0
    ordinal = 0

    def flush(end_index: int) -> None:
        nonlocal buffer, ordinal
        content = "\n".join(buffer).strip("\n")
        if content.strip():
            heading = " > ".join(title for _, title in stack) or None
            ordinal += 1
            last_line = body_start_line + end_index
            chunks.append(
                ChunkData(
                    heading=heading,
                    content=content.strip(),
                    ordinal=ordinal,
                    chunk_hash=compute_chunk_hash(heading, content),
                    line_start=body_start_line + buffer_start,
                    line_end=last_line,
                )
            )
        buffer = []

    for index, line in enumerate(body_lines):
        if index in heading_at:
            if buffer:
                flush(index - 1)
            level, text = heading_at[index]
            if level == 1:
                h1_titles.append(text)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            buffer_start = index + 1
        else:
            buffer.append(line)

    if buffer:
        flush(len(body_lines) - 1)
    return chunks, h1_titles


def parse_document(path: str, text: str, *, parser_version: str = PARSER_VERSION) -> ParsedDocument:
    frontmatter, body, body_start_line = _split_frontmatter(text)
    body_normalized = normalize_body(body)
    body_lines = body_normalized.split("\n") if body_normalized else []

    chunks, h1_titles = _build_chunks(body_lines, body_start_line)

    title = str(frontmatter.get("title") or "").strip() or (h1_titles[0] if h1_titles else "")
    if not title:
        title = Path(path).stem

    tags = _normalize_tags(frontmatter.get("tags") or frontmatter.get("tag"))
    occurred_on, parse_warnings = resolve_occurred_on(frontmatter, path)
    date_source = _date_source(frontmatter, path, occurred_on)

    links: list[LinkData] = []
    seen: set[tuple[str, str | None]] = set()
    for match in _WIKILINK.finditer(body_normalized):
        target = match.group(1).strip()
        link_text = match.group(2).strip() if match.group(2) else None
        key = (target, link_text)
        if target and key not in seen:
            seen.add(key)
            links.append(LinkData(target_path=target, link_text=link_text))

    search_text = build_search_text(
        "\n".join([title, " ".join(tags), body_normalized])
    )

    return ParsedDocument(
        path=path,
        title=title,
        body=body_normalized,
        frontmatter=frontmatter,
        tags=tags,
        occurred_on=occurred_on,
        links=links,
        chunks=chunks,
        content_hash=compute_content_hash(body_normalized, frontmatter, parser_version),
        search_text=search_text,
        parser_version=parser_version,
        extra={"h1_titles": h1_titles},
        body_start_line=body_start_line,
        date_source=date_source,
        parse_warnings=parse_warnings,
        task_occurrences=[
            TaskOccurrenceData(
                line_number=item.line_number, checked=item.checked, text=item.text
            )
            for item in extract_occurrences(
                body_lines, body_start_line=body_start_line
            )
        ],
    )


def image_links(body: str) -> list[str]:
    """提取 `![[img.png]]` 资源引用（资产代理用）。"""
    return [match.group(1).strip() for match in _IMAGE_LINK.finditer(body)]
