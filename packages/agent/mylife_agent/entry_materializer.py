"""条目物化（ARCHITECTURE §7.1）。

在 REVIEWING 之前把 structured_json 中的 error/method/action 条目转成
`review_entries` 行；`canonical_key` 由确定性代码计算，不由模型提供。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from mylife_runtime.hashing import canonical_key, root_cause_fingerprint

#: section_key → item_type（§5.1 与 ITEM_SCHEMAS 的对应关系）。
SECTION_ITEM_TYPES: dict[str, str] = {
    "mistakes": "error",
    "methods": "method",
    "tomorrow_actions": "action",
    "next_week_actions": "action",
}

#: 每种条目用于 canonical_key 的标题字段。
TITLE_FIELDS: dict[str, tuple[str, ...]] = {
    "error": ("root_cause", "event"),
    "method": ("name",),
    "action": ("text",),
}


@dataclass(slots=True)
class MaterializedEntry:
    section_key: str
    item_type: str
    canonical_key: str
    fields: dict[str, Any]
    source_path: str | None = None
    line_range: list[int] | None = None
    evidence_id: str | None = None
    source_date: Any | None = None
    confidence: float | None = None
    root_cause_fingerprint: str | None = field(default=None)


def _title_for(item_type: str, fields: dict[str, Any]) -> str:
    for key in TITLE_FIELDS.get(item_type, ()):
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def materialize_entries(
    sections: Sequence[dict[str, Any]],
    *,
    evidence_by_path: dict[str, dict[str, Any]] | None = None,
    source_by_key: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[MaterializedEntry]:
    """把 Draft sections 的 structured items 转成可写入 review_entries 的行。

    `source_by_key` 为 `(item_type, canonical_key) → {source_path, line_range,
    source_date, evidence_id}`，用于来自笔记正文的确定性抽取条目（G4 可追溯）。
    """
    evidence_by_path = evidence_by_path or {}
    source_by_key = source_by_key or {}
    entries: list[MaterializedEntry] = []
    seen: set[tuple[str, str, str]] = set()

    for section in sections:
        section_key = section.get("section_key", "")
        item_type = SECTION_ITEM_TYPES.get(section_key)
        if item_type is None:
            continue
        structured = section.get("structured") or {}
        for fields in structured.get("items", []):
            if not isinstance(fields, dict):
                continue
            title = _title_for(item_type, fields)
            if not title:
                continue
            key = canonical_key(item_type, title)
            marker = (section_key, item_type, key)
            if marker in seen:  # 同一版本内幂等
                continue
            seen.add(marker)
            evidence = _evidence_for(fields, evidence_by_path)
            source = source_by_key.get((item_type, key)) or {}
            if evidence is None and source:
                evidence = source
            source_date = evidence.get("source_date") if evidence else None
            if isinstance(source_date, str):
                from datetime import date as _date

                try:
                    source_date = _date.fromisoformat(source_date)
                except ValueError:
                    source_date = None
            entries.append(
                MaterializedEntry(
                    section_key=section_key,
                    item_type=item_type,
                    canonical_key=key,
                    fields=dict(fields),
                    source_path=evidence.get("source_path") if evidence else None,
                    line_range=evidence.get("line_range") if evidence else None,
                    evidence_id=evidence.get("evidence_id") if evidence else None,
                    source_date=source_date,
                    root_cause_fingerprint=(
                        root_cause_fingerprint(str(fields.get("root_cause")))
                        if item_type == "error" and fields.get("root_cause")
                        else None
                    ),
                )
            )
    return entries


def _evidence_for(
    fields: dict[str, Any], evidence_by_path: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    evidence_id = fields.get("evidence_id")
    if isinstance(evidence_id, str) and evidence_id in evidence_by_path:
        return evidence_by_path[evidence_id]
    path = fields.get("source_path")
    if isinstance(path, str):
        return evidence_by_path.get(path)
    return None
