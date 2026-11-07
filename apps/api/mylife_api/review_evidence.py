"""复盘取证（ARCHITECTURE §2.3/§7.1；AC-34/35）。

确定性 SQL 取证：任务台账、日记、当天变更的学习笔记、历史复盘指针。
所有证据带 snapshot_id 与 document_content_hash，供 G3 复算。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from mylife_runtime.hashing import quote_hash
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_QUOTE_CHARS = 500


@dataclass(slots=True)
class EvidenceRow:
    evidence_id: str
    source_type: str
    quote: str
    quote_hash: str
    source_path: str | None = None
    source_date: date | None = None
    occurred_on: date | None = None
    document_content_hash: str | None = None
    heading: str | None = None
    line_range: list[int] | None = None
    document_id: UUID | None = None
    chunk_id: UUID | None = None
    tags: list[str] = field(default_factory=list)
    title: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "quote": self.quote,
            "quote_hash": self.quote_hash,
            "source_path": self.source_path,
            "source_date": self.source_date,
            "occurred_on": self.occurred_on,
            "document_content_hash": self.document_content_hash,
            "heading": self.heading,
            "line_range": self.line_range,
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "tags": self.tags,
        }


class EvidenceCollector:
    """按 section 收集证据，维护 evidence_id 递增与全局上限。"""

    def __init__(self, *, parser_version: str, max_total: int = 80) -> None:
        self.parser_version = parser_version
        self.max_total = max_total
        self.items: list[EvidenceRow] = []
        self.by_section: dict[str, list[str]] = {}

    def add(
        self,
        section_key: str,
        *,
        source_type: str,
        quote: str,
        source_path: str | None = None,
        source_date: date | None = None,
        occurred_on: date | None = None,
        document_content_hash: str | None = None,
        heading: str | None = None,
        line_range: list[int] | None = None,
        document_id: UUID | None = None,
        chunk_id: UUID | None = None,
        tags: list[str] | None = None,
        title: str | None = None,
    ) -> str | None:
        if len(self.items) >= self.max_total:
            return None
        evidence_id = f"e{len(self.items) + 1}"
        row = EvidenceRow(
            evidence_id=evidence_id,
            source_type=source_type,
            quote=quote[:MAX_QUOTE_CHARS],
            quote_hash=quote_hash(self.parser_version, quote[:MAX_QUOTE_CHARS]),
            source_path=source_path,
            source_date=source_date,
            occurred_on=occurred_on,
            document_content_hash=document_content_hash,
            heading=heading,
            line_range=line_range,
            document_id=document_id,
            chunk_id=chunk_id,
            tags=tags or [],
            title=title,
        )
        self.items.append(row)
        self.by_section.setdefault(section_key, []).append(evidence_id)
        return evidence_id


async def collect_carry_tasks(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    period_start: date,
    collector: EvidenceCollector,
    section_key: str = "today_tasks",
    limit: int = 12,
) -> list[dict[str, Any]]:
    """「以往顺延」：open 且 first_seen < period_start（DATA-MODEL §10.2）。"""
    result = await session.execute(
        text(
            "SELECT id, text, first_seen, last_seen, source_path, line_number "
            "FROM tasks WHERE workspace_id = :ws AND status = 'open' "
            "  AND first_seen < :start ORDER BY first_seen ASC LIMIT :limit"
        ),
        {"ws": workspace_id, "start": period_start, "limit": limit},
    )
    rows = [dict(row._mapping) for row in result]
    for row in rows:
        collector.add(
            section_key,
            source_type="task",
            quote=row["text"][:MAX_QUOTE_CHARS],
            source_path=row["source_path"],
            source_date=row["first_seen"],
            line_range=[row["line_number"], row["line_number"]] if row.get("line_number") else None,
        )
    return rows


async def collect_period_tasks(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    period_start: date,
    period_end: date,
    collector: EvidenceCollector,
    section_key: str = "today_tasks",
) -> dict[str, list[dict[str, Any]]]:
    """当日 occurrence 分完成/未完成两组。"""
    result = await session.execute(
        text(
            "SELECT o.checked, o.raw_text, o.line_number, o.occurrence_date, d.path, "
            "       d.content_hash, d.id AS document_id "
            "FROM task_occurrences o JOIN documents d ON d.id = o.document_id "
            "WHERE o.workspace_id = :ws AND o.occurrence_date BETWEEN :start AND :end "
            "ORDER BY o.occurrence_date ASC, d.path ASC, o.line_number ASC"
        ),
        {"ws": workspace_id, "start": period_start, "end": period_end},
    )
    groups: dict[str, list[dict[str, Any]]] = {"completed": [], "open": []}
    for row in result:
        bucket = "completed" if row.checked else "open"
        if len(groups[bucket]) >= 12:
            continue
        # 台账行的行号是文件绝对行号（含 frontmatter），与正文行号基准不同，
        # 因此标记为 task 证据（G3c 只校验 document/chunk）。
        evidence_id = collector.add(
            section_key,
            source_type="task",
            quote=row.raw_text[:MAX_QUOTE_CHARS],
            source_path=row.path,
            source_date=row.occurrence_date,
            occurred_on=row.occurrence_date,
            document_content_hash=row.content_hash,
            line_range=[row.line_number, row.line_number],
            document_id=row.document_id,
        )
        groups[bucket].append(
            {
                "text": row.raw_text,
                "source_date": row.occurrence_date,
                "source_path": row.path,
                "line_range": [row.line_number, row.line_number],
                "evidence_id": evidence_id,
            }
        )
    return groups


async def collect_journal(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    period_key: date,
    collector: EvidenceCollector,
    section_key: str = "journal_events",
    limit: int = 12,
) -> list[dict[str, Any]]:
    """按日期匹配 `日记/` 下的文档（AC-34）。"""
    result = await session.execute(
        text(
            "SELECT id, path, title, content, content_hash, occurred_on, tags "
            "FROM documents WHERE workspace_id = :ws AND source_type = 'user' "
            "  AND occurred_on = :period AND (path = '日记' OR path LIKE '日记/%') "
            "ORDER BY path ASC LIMIT :limit"
        ),
        {"ws": workspace_id, "period": period_key, "limit": limit},
    )
    rows = [dict(row._mapping) for row in result]
    for row in rows:
        body = (row["content"] or "").strip()
        if not body:
            continue
        collector.add(
            section_key,
            source_type="document",
            quote=body[:MAX_QUOTE_CHARS],
            source_path=row["path"],
            source_date=period_key,
            occurred_on=row["occurred_on"],
            document_content_hash=row["content_hash"],
            line_range=[1, max(1, body.count("\n") + 1)],
            document_id=row["id"],
            tags=list(row["tags"] or []),
            title=row["title"],
        )
    return rows


async def collect_learning_changes(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    period_key: date,
    reviews_root: str,
    collector: EvidenceCollector,
    section_key: str = "learning_summary",
    limit: int = 12,
) -> list[dict[str, Any]]:
    """当天变更的学习笔记（AC-35）：排除日记与复盘产物。"""
    result = await session.execute(
        text(
            "SELECT DISTINCT d.id, d.path, d.title, d.content, d.content_hash, "
            "       d.occurred_on, d.tags "
            "FROM document_changes c JOIN documents d ON d.id = c.document_id "
            "WHERE c.workspace_id = :ws AND c.source_date = :period "
            "  AND c.change_type IN ('added','modified') AND d.source_type = 'user' "
            "  AND d.path NOT LIKE '日记/%' AND d.path NOT LIKE :root "
            "ORDER BY d.path ASC LIMIT :limit"
        ),
        {
            "ws": workspace_id,
            "period": period_key,
            "root": f"{reviews_root.rstrip('/')}/%",
            "limit": limit,
        },
    )
    rows = [dict(row._mapping) for row in result]
    for row in rows:
        body = (row["content"] or "").strip()
        if not body:
            continue
        collector.add(
            section_key,
            source_type="document",
            quote=body[:MAX_QUOTE_CHARS],
            source_path=row["path"],
            source_date=period_key,
            occurred_on=row["occurred_on"],
            document_content_hash=row["content_hash"],
            line_range=[1, max(1, body.count("\n") + 1)],
            document_id=row["id"],
            tags=list(row["tags"] or []),
            title=row["title"],
        )
    return rows


async def collect_prior_reviews(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    kind: str,
    period_key: str,
    collector: EvidenceCollector,
    section_key: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """PRIOR_REVIEW：只提供指针（标题/版本/路径），不复制正文。"""
    result = await session.execute(
        text(
            "SELECT id, period_key, title, version, git_path FROM reviews "
            "WHERE workspace_id = :ws AND kind = :kind AND period_key < :period "
            "  AND status IN ('delivered','approved') "
            "ORDER BY period_key DESC LIMIT :limit"
        ),
        {"ws": workspace_id, "kind": kind, "period": period_key, "limit": limit},
    )
    rows = [dict(row._mapping) for row in result]
    for row in rows:
        collector.add(
            section_key,
            source_type="review",
            quote=f"历史复盘 {row['period_key']} v{row['version']}：{row['title']}",
            source_path=row.get("git_path"),
            source_date=None,
            document_content_hash=row["id"].hex,
        )
    return rows


async def collect_entries_for_period(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    kind: str,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    """周/年 rollup 只读 current version 的 review_entries。"""
    result = await session.execute(
        text(
            "SELECT e.section_key, e.item_type, e.canonical_key, e.fields_json, "
            "       e.source_path, e.line_start, e.line_end, e.source_date "
            "FROM review_entries e JOIN reviews r ON r.id = e.review_id "
            "WHERE e.workspace_id = :ws AND r.kind = :kind "
            "  AND r.period_key BETWEEN :start AND :end "
            "  AND e.version = r.version AND r.superseded_by IS NULL"
        ),
        {
            "ws": workspace_id,
            "kind": kind,
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
    )
    rows = []
    for row in result:
        payload = dict(row._mapping)
        fields = payload.pop("fields_json")
        payload["fields"] = json.loads(fields) if isinstance(fields, str) else fields
        rows.append(payload)
    return rows


async def collect_sections_for_period(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    kind: str,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT s.section_key, s.structured_json, r.period_key "
            "FROM review_sections s JOIN reviews r ON r.id = s.review_id "
            "WHERE s.workspace_id = :ws AND r.kind = :kind "
            "  AND r.period_key BETWEEN :start AND :end "
            "  AND s.version = r.version AND r.superseded_by IS NULL"
        ),
        {
            "ws": workspace_id,
            "kind": kind,
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
    )
    rows = []
    for row in result:
        payload = dict(row._mapping)
        structured = payload.pop("structured_json")
        payload["structured"] = (
            json.loads(structured) if isinstance(structured, str) else structured
        )
        rows.append(payload)
    return rows
