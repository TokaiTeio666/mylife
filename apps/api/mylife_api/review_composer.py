"""复盘草稿装配（ARCHITECTURE §3.3；AC-36/37/38）。

Phase 3 的 Writer 是**确定性装配器**：不调用生成式 LLM 产生事实，只把
取证结果按 §5.1 章节契约组织成 Draft；无证据章节一律使用允许哨兵。
真实 LLM 写作留作 `model_profile` 扩展点（Phase 4+）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from mylife_contract.constants import SECTION_ORDINALS, SENTINELS
from mylife_contract.review import SECTION_SCHEMAS

from mylife_api.review_evidence import EvidenceCollector


@dataclass(slots=True)
class ComposedSection:
    section_key: str
    ordinal: int
    title: str
    content_md: str
    structured: dict[str, Any] = field(default_factory=dict)
    claim_ids: list[str] = field(default_factory=list)
    sentinel_used: bool = False
    degraded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "section_key": self.section_key,
            "ordinal": self.ordinal,
            "title": self.title,
            "content_md": self.content_md,
            "structured": self.structured,
            "claim_ids": self.claim_ids,
            "sentinel_used": self.sentinel_used,
            "degraded": self.degraded,
        }


@dataclass(slots=True)
class ComposedDraft:
    sections: list[ComposedSection]
    claims: list[dict[str, Any]] = field(default_factory=list)
    entries: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    coverage_gaps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def no_evidence_ratio(self) -> float:
        if not self.sections:
            return 1.0
        sentinel = sum(1 for section in self.sections if section.sentinel_used)
        return round(sentinel / len(self.sections), 4)

    def as_sections(self) -> list[dict[str, Any]]:
        return [section.as_dict() for section in self.sections]


def _section_meta(kind: str) -> list[tuple[int, str, str, int]]:
    return list(SECTION_ORDINALS[kind])


def _sentinel_section(ordinal: int, key: str, title: str) -> ComposedSection:
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md=SENTINELS[key],
        structured={},
        sentinel_used=True,
    )


def _claim(
    draft: ComposedDraft,
    *,
    section_key: str,
    text: str,
    evidence_ids: list[str],
    claim_type: str = "fact",
) -> str:
    claim_id = f"c{len(draft.claims) + 1}"
    index = len(draft.claims) + 1
    draft.claims.append(
        {
            "claim_id": claim_id,
            "section_key": section_key,
            "text": text,
            "evidence_ids": evidence_ids,
            "citation_indexes": [index],
            "claim_type": claim_type,
        }
    )
    return claim_id


def _cite(
    draft: ComposedDraft,
    collector: EvidenceCollector,
    *,
    claim_id: str,
    evidence_id: str,
) -> None:
    row = next((item for item in collector.items if item.evidence_id == evidence_id), None)
    if row is None:
        return
    draft.citations.append(
        {
            "claim_id": claim_id,
            "evidence_id": evidence_id,
            "source_path": row.source_path,
            "source_date": row.source_date,
            "document_content_hash": row.document_content_hash,
            "line_range": row.line_range,
            "quote": row.quote,
            "quote_hash": row.quote_hash,
        }
    )


# ---------------------------------------------------------------------------
# daily
# ---------------------------------------------------------------------------


def compose_daily(
    *,
    period_key: date,
    collector: EvidenceCollector,
    period_tasks: dict[str, list[dict[str, Any]]],
    carried_tasks: list[dict[str, Any]],
    journal_docs: list[dict[str, Any]],
    learning_docs: list[dict[str, Any]],
    entries: list[dict[str, Any]] | None = None,
) -> ComposedDraft:
    draft = ComposedDraft(sections=[])
    entries = entries or []
    evidence_ids = {item.evidence_id: item for item in collector.items}
    by_section = collector.by_section

    for ordinal, key, title, _cap in _section_meta("daily"):
        if key == "today_tasks":
            section = _today_tasks(
                draft, collector, ordinal, key, title, period_tasks, carried_tasks
            )
        elif key == "learning_summary":
            section = _learning_summary(draft, collector, ordinal, key, title, learning_docs)
        elif key == "journal_events":
            section = _journal_events(draft, collector, ordinal, key, title, journal_docs)
        elif key == "mistakes":
            section = _item_section(draft, ordinal, key, title, entries, "error")
        elif key == "methods":
            section = _item_section(draft, ordinal, key, title, entries, "method")
        elif key == "tomorrow_actions":
            section = _action_section(draft, ordinal, key, title, entries, "action")
        else:  # one_line_summary
            section = _one_line_summary(draft, ordinal, key, title)
        draft.sections.append(section)

    # 引用闭包：只为实际引用的 evidence 生成 citation
    cited = {claim["claim_id"] for claim in draft.claims}
    draft.citations = [item for item in draft.citations if item["claim_id"] in cited]
    _ = evidence_ids, by_section
    return draft


def _today_tasks(
    draft: ComposedDraft,
    collector: EvidenceCollector,
    ordinal: int,
    key: str,
    title: str,
    period_tasks: dict[str, list[dict[str, Any]]],
    carried_tasks: list[dict[str, Any]],
) -> ComposedSection:
    completed = period_tasks.get("completed", [])
    open_tasks = period_tasks.get("open", [])
    carried = [
        {
            "text": row["text"],
            "source_date": row["first_seen"],
            "source_path": row.get("source_path"),
            "line_range": [row["line_number"], row["line_number"]]
            if row.get("line_number")
            else None,
            "task_id": str(row["id"]),
        }
        for row in carried_tasks
    ]
    if not (completed or open_tasks or carried):
        return _sentinel_section(ordinal, key, title)

    structured = {
        "completed": [
            {
                k: item[k]
                for k in ("text", "source_date", "source_path", "line_range", "evidence_id")
            }
            for item in completed
        ],
        "open": [
            {
                k: item[k]
                for k in ("text", "source_date", "source_path", "line_range", "evidence_id")
            }
            for item in open_tasks
        ],
        "carried_over": carried,
    }
    lines = ["### 已完成", ""]
    lines.extend(f"- {item['text']}" for item in completed) if completed else lines.append("- 无")
    lines.extend(["", "### 未完成", ""])
    lines.extend(f"- {item['text']}" for item in open_tasks) if open_tasks else lines.append("- 无")
    lines.extend(["", "### 以往顺延", ""])
    lines.extend(
        f"- {item['text']}（自 {item['source_date']}）" for item in carried
    ) if carried else lines.append("- 无")

    evidence_ids = [
        item["evidence_id"] for item in (*completed, *open_tasks) if item.get("evidence_id")
    ]
    claim_id = None
    if evidence_ids:
        claim_id = _claim(
            draft,
            section_key=key,
            text=(
                f"本日共完成 {len(completed)} 项、未完成 {len(open_tasks)} 项、"
                f"顺延 {len(carried)} 项"
            ),
            evidence_ids=evidence_ids,
            claim_type="task_status",
        )
        for evidence_id in evidence_ids:
            _cite(draft, collector, claim_id=claim_id, evidence_id=evidence_id)

    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md="\n".join(lines).strip(),
        structured=structured,
        claim_ids=[claim_id] if claim_id else [],
    )


def _learning_summary(
    draft: ComposedDraft,
    collector: EvidenceCollector,
    ordinal: int,
    key: str,
    title: str,
    learning_docs: list[dict[str, Any]],
) -> ComposedSection:
    if not learning_docs:
        return _sentinel_section(ordinal, key, title)

    evidence_ids = collector.by_section.get(key, [])
    topics = [doc["title"] or doc["path"] for doc in learning_docs]
    field_evidence = {
        field: evidence_ids
        for field in (
            "topic",
            "concept_or_method",
            "output",
            "open_questions",
        )
    }
    structured = {
        "topic": "、".join(topics),
        "concept_or_method": f"阅读并整理了 {len(learning_docs)} 篇学习笔记",
        "output": "、".join(doc["path"] for doc in learning_docs),
        "open_questions": "待补充",
        "field_evidence": field_evidence,
    }
    lines = [
        f"- 主题：{structured['topic']}",
        f"- 概念/方法：{structured['concept_or_method']}",
        f"- 产出：{structured['output']}",
        f"- 待解决：{structured['open_questions']}",
    ]
    claim_id = None
    if evidence_ids:
        claim_id = _claim(
            draft,
            section_key=key,
            text=f"本日学习笔记变更 {len(learning_docs)} 篇",
            evidence_ids=evidence_ids,
        )
        for evidence_id in evidence_ids:
            _cite(draft, collector, claim_id=claim_id, evidence_id=evidence_id)
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md="\n".join(lines),
        structured=structured,
        claim_ids=[claim_id] if claim_id else [],
    )


def _journal_events(
    draft: ComposedDraft,
    collector: EvidenceCollector,
    ordinal: int,
    key: str,
    title: str,
    journal_docs: list[dict[str, Any]],
) -> ComposedSection:
    if not journal_docs:
        return _sentinel_section(ordinal, key, title)

    evidence_ids = collector.by_section.get(key, [])
    items = []
    lines: list[str] = []
    for doc in journal_docs:
        first_line = next(
            (
                line.strip("# ").strip()
                for line in (doc["content"] or "").splitlines()
                if line.strip()
            ),
            "",
        )
        items.append(
            {
                "event": first_line[:120] or "日记",
                "person": "未提及",
                "decision": "未提及",
                "emotion": "未提及",
                "result": "未提及",
            }
        )
        lines.append(f"- {first_line[:120] or '日记'}（{doc['path']}）")
    claim_id = None
    if evidence_ids:
        claim_id = _claim(
            draft,
            section_key=key,
            text=f"本日日记 {len(journal_docs)} 篇",
            evidence_ids=evidence_ids,
            claim_type="event",
        )
        for evidence_id in evidence_ids:
            _cite(draft, collector, claim_id=claim_id, evidence_id=evidence_id)
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md="\n".join(lines),
        structured={"items": items},
        claim_ids=[claim_id] if claim_id else [],
    )


def _item_section(
    draft: ComposedDraft,
    ordinal: int,
    key: str,
    title: str,
    entries: list[dict[str, Any]],
    item_type: str,
) -> ComposedSection:
    rows = [entry for entry in entries if entry.get("item_type") == item_type]
    if not rows:
        return _sentinel_section(ordinal, key, title)
    lines: list[str] = []
    structured_items: list[dict[str, Any]] = []
    for index, entry in enumerate(rows, start=1):
        fields = entry.get("fields") or {}
        structured_items.append(fields)
        heading = fields.get("name") or fields.get("event") or fields.get("text") or "条目"
        location = ""
        if entry.get("source_path"):
            line = (entry.get("line_range") or [None, None])[0]
            location = f"（{entry['source_path']}{f'#L{line}' if line else ''}）"
        lines.append(f"{index}. {heading}{location}")
        for field_name, value in fields.items():
            if field_name in {"event", "name", "text", "evidence_id", "source_path"}:
                continue
            if isinstance(value, list):
                value = "、".join(str(item) for item in value)
            lines.append(f"   - {field_name}：{value}")
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md="\n".join(lines),
        structured={"items": structured_items},
    )


def _action_section(
    draft: ComposedDraft,
    ordinal: int,
    key: str,
    title: str,
    entries: list[dict[str, Any]],
    item_type: str,
) -> ComposedSection:
    rows = [entry for entry in entries if entry.get("item_type") == item_type]
    if not rows:
        return _sentinel_section(ordinal, key, title)
    lines: list[str] = []
    structured_items: list[dict[str, Any]] = []
    for entry in rows:
        fields = entry.get("fields") or {}
        structured_items.append(fields)
        lines.append(
            f"- [ ] {fields.get('text', '')}"
            f"（触发：{fields.get('trigger', '')}；验证：{fields.get('verification', '')}）"
        )
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md="\n".join(lines),
        structured={"items": structured_items},
    )


def _one_line_summary(draft: ComposedDraft, ordinal: int, key: str, title: str) -> ComposedSection:
    parts = [
        section.content_md.splitlines()[0]
        for section in draft.sections
        if not section.sentinel_used and section.content_md
    ]
    if not parts:
        return _sentinel_section(ordinal, key, title)
    summary = f"本日要点：{parts[0][:80]}"
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md=summary,
        structured={"summary": summary},
    )


# ---------------------------------------------------------------------------
# weekly / yearly
# ---------------------------------------------------------------------------


def compose_weekly(
    *,
    period_key: str,
    collector: EvidenceCollector,
    ledger: dict[str, Any],
    recurring: list[dict[str, Any]],
    method_landing: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    coverage_gaps: list[dict[str, Any]],
) -> ComposedDraft:
    draft = ComposedDraft(sections=[], coverage_gaps=coverage_gaps)
    for ordinal, key, title, _cap in _section_meta("weekly"):
        if key == "completion_rate":
            structured = {
                "completed": ledger["completed"],
                "total": ledger["total"],
                "rate": ledger["rate"],
                "ledger_recalc": ledger,
            }
            content = (
                f"- 已完成：{ledger['completed']}\n"
                f"- 总数：{ledger['total']}\n"
                f"- 完成率：{ledger['rate']:.2%}"
            )
            section = ComposedSection(
                section_key=key,
                ordinal=ordinal,
                title=title,
                content_md=content,
                structured=structured,
            )
        elif key == "recurring_mistakes":
            section = _recurring_section(ordinal, key, title, recurring)
        elif key == "method_landing":
            section = _method_landing_section(ordinal, key, title, method_landing)
        else:
            section = _action_section(draft, ordinal, key, title, actions, "action")
        draft.sections.append(section)
    return draft


def _recurring_section(
    ordinal: int, key: str, title: str, recurring: list[dict[str, Any]]
) -> ComposedSection:
    if not recurring:
        return _sentinel_section(ordinal, key, title)
    lines = [f"- {item['canonical_key'][:12]}…：{item['occurrences']} 次" for item in recurring]
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md="\n".join(lines),
        structured={"top_n": 10, "items": recurring},
    )


def _method_landing_section(
    ordinal: int, key: str, title: str, rows: list[dict[str, Any]]
) -> ComposedSection:
    if not rows:
        return _sentinel_section(ordinal, key, title)
    lines = [
        f"- {row['method']}：行动 {row['action_count']} 项，状态 {row['verification_status']}"
        for row in rows
    ]
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md="\n".join(lines),
        structured={"items": rows},
    )


def compose_yearly(
    *,
    period_key: str,
    collector: EvidenceCollector,
    learning_map: dict[str, list[str]],
    timeline: list[dict[str, Any]],
    top_mistakes: list[dict[str, Any]],
    method_system: list[dict[str, Any]],
    direction: dict[str, str],
    coverage_gaps: list[dict[str, Any]],
) -> ComposedDraft:
    draft = ComposedDraft(sections=[], coverage_gaps=coverage_gaps)
    for ordinal, key, title, _cap in _section_meta("yearly"):
        if key == "learning_map":
            structured = learning_map
            content = "\n".join(f"- {item}" for item in (learning_map.get("topics") or []))
            section = ComposedSection(
                section_key=key,
                ordinal=ordinal,
                title=title,
                content_md=content,
                structured=structured,
                sentinel_used=not bool(content),
            )
            if section.sentinel_used:
                section.content_md = SENTINELS[key]
        elif key == "event_timeline":
            section = _timeline_section(ordinal, key, title, timeline)
        elif key == "top_mistakes":
            section = _top_mistakes_section(ordinal, key, title, top_mistakes)
        elif key == "method_system":
            section = _method_system_section(ordinal, key, title, method_system)
        else:
            content = (
                f"- 方向：{direction.get('direction', '')}\n"
                f"- 依据：{direction.get('basis', '')}\n"
                f"- 第一步：{direction.get('first_action', '')}"
            )
            sentinel = not direction.get("direction")
            section = ComposedSection(
                section_key=key,
                ordinal=ordinal,
                title=title,
                content_md=SENTINELS[key] if sentinel else content,
                structured={} if sentinel else direction,
                sentinel_used=sentinel,
            )
        draft.sections.append(section)
    return draft


def _timeline_section(
    ordinal: int, key: str, title: str, rows: list[dict[str, Any]]
) -> ComposedSection:
    if not rows:
        return _sentinel_section(ordinal, key, title)
    ordered = sorted(rows, key=lambda item: item["date"])
    lines = [f"- {item['date']}：{item['event']}（{item['result']}）" for item in ordered]
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md="\n".join(lines),
        structured={"items": ordered},
    )


def _top_mistakes_section(
    ordinal: int, key: str, title: str, rows: list[dict[str, Any]]
) -> ComposedSection:
    if not rows:
        return _sentinel_section(ordinal, key, title)
    ordered = sorted(rows, key=lambda item: (-item["occurrences"], item["canonical_key"]))[:10]
    items = [
        {
            "rank": index,
            "item": row.get("item") or row["canonical_key"][:12],
            "occurrences": row["occurrences"],
            "cost": row.get("cost") or "未记录",
            "source": row.get("source_path") or "—",
        }
        for index, row in enumerate(ordered, start=1)
    ]
    lines = [f"{item['rank']}. {item['item']}：{item['occurrences']} 次" for item in items]
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md="\n".join(lines),
        structured={"items": items},
    )


def _method_system_section(
    ordinal: int, key: str, title: str, rows: list[dict[str, Any]]
) -> ComposedSection:
    if not rows:
        return _sentinel_section(ordinal, key, title)
    items = [
        {
            "category": row.get("category") or "未分类",
            "name": row.get("name") or row["canonical_key"][:12],
            "scenario": row.get("scenario") or "未记录",
            "verification": row.get("verification") or "未记录",
        }
        for row in rows
    ]
    lines = [f"- {item['category']}：{item['name']}" for item in items]
    return ComposedSection(
        section_key=key,
        ordinal=ordinal,
        title=title,
        content_md="\n".join(lines),
        structured={"items": items},
    )


def content_hash_of(content_md: str) -> str:
    return hashlib.sha256(content_md.encode("utf-8")).hexdigest()


def draft_fingerprint(draft: ComposedDraft) -> str:
    payload = json.dumps(
        [section.as_dict() for section in draft.sections],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_section_payloads(draft: ComposedDraft, kind: str) -> list[str]:
    """G2 预检：非哨兵章节的 structured 必须能通过 SECTION_SCHEMAS。"""
    errors: list[str] = []
    for section in draft.sections:
        if section.sentinel_used:
            continue
        model = SECTION_SCHEMAS.get(kind, {}).get(section.section_key)
        if model is None:
            errors.append(f"unknown section {kind}/{section.section_key}")
            continue
        try:
            model.model_validate(section.structured)
        except Exception as exc:  # noqa: BLE001 - 汇总为可读错误
            errors.append(f"{section.section_key}: {exc}")
    return errors
