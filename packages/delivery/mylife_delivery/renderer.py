"""复盘 Markdown 渲染（ARCHITECTURE §8.1；DIRECTORY-CONSTRAINTS §4.3/§4.4）。

只做确定性拼装与哈希，不含业务语义；`content_md` 由 sections 按 ordinal 升序
拼装，同一输入必须字节级可复现。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

GENERATOR = "mylife/0.4"


@dataclass(frozen=True, slots=True)
class RenderedReview:
    content_md: str
    content_hash: str


def _frontmatter(payload: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in payload.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (datetime,)):
            rendered = (
                value.astimezone(UTC)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
        else:
            rendered = str(value)
        if any(
            char in rendered for char in (":", "#", "\n")
        ) and not rendered.startswith('"'):
            rendered = json.dumps(rendered, ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines)


def render_review_markdown(
    *,
    kind: str,
    period_key: str,
    review_id: str,
    version: int,
    snapshot_id: str | None,
    generated_at: datetime,
    sections: Sequence[dict[str, Any]],
) -> RenderedReview:
    """按 ordinal 拼装复盘 Markdown 并计算 sha256。"""
    ordered = sorted(sections, key=lambda item: int(item.get("ordinal", 0)))
    frontmatter = _frontmatter(
        {
            "mylife": True,
            "kind": kind,
            "period_key": period_key,
            "review_id": review_id,
            "version": version,
            "snapshot_id": snapshot_id or "",
            "generated_at": generated_at,
            "generator": GENERATOR,
        }
    )
    blocks: list[str] = [frontmatter]
    for section in ordered:
        title = section.get("title") or section.get("section_key", "")
        content = (section.get("content_md") or "").strip()
        blocks.append(f"## {title}\n\n{content}")
    content_md = "\n\n".join(blocks) + "\n"
    return RenderedReview(
        content_md=content_md,
        content_hash=hashlib.sha256(content_md.encode("utf-8")).hexdigest(),
    )


def review_path(reviews_root: str, kind: str, period_key: str) -> str:
    """`{reviews_root}/每日复盘/YYYY/YYYY-MM-DD.md`（DIRECTORY-CONSTRAINTS §4）。"""
    root = (reviews_root or "wiki/复盘").rstrip("/")
    if kind == "daily":
        return f"{root}/每日复盘/{period_key[:4]}/{period_key}.md"
    if kind == "weekly":
        return f"{root}/每周复盘/{period_key[:4]}/{period_key}.md"
    return f"{root}/年度复盘/{period_key}.md"


def topic_path(reviews_root: str, topic: str) -> str:
    root = (reviews_root or "wiki/复盘").rstrip("/")
    names = {
        "errors": "错误清单",
        "methods": "正确方法论",
        "actions": "行动清单",
    }
    return f"{root}/专题/{names.get(topic, topic)}.md"
