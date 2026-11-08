"""专题页 Markdown 渲染（ARCHITECTURE §7.3；DIRECTORY-CONSTRAINTS §4）。

输入是**数据行的普通 dict**，不依赖 ORM；同一输入必须字节级可复现。
`pending` 与 `dropped` 不进入专题页；`resolved` 单列“已解决”。
冲突字段必须并列展示，不得静默择一（FX-07）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from mylife_contract.constants import TOPIC_FILE_NAMES

from mylife_delivery.markers import wrap_auto_region

GENERATOR = "mylife/0.4"

STATUS_LABELS = {
    "open": "待解决",
    "stale": "陈旧（降权）",
    "resolved": "已解决",
}


def _date(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value or "")


def _frontmatter(payload: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in payload.items():
        rendered = str(value)
        if any(char in rendered for char in (":", "#", "\n")):
            import json

            rendered = json.dumps(rendered, ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines)


def _line_range(item: dict[str, Any]) -> str:
    value = item.get("line_range")
    if not value or len(value) < 2:
        return ""
    start, end = value[0], value[1]
    # 0/None 是“无行号”的哨兵（迁移 0005 把 NULL 回填为 0）。
    if start in (None, 0) or end is None:
        return ""
    return f"#L{int(start)}-L{int(end)}"


def _occurrence_lines(item: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for occurrence in item.get("occurrences") or []:
        path = occurrence.get("source_path") or ""
        quote = (occurrence.get("quote") or "").replace("\n", " ").strip()
        if len(quote) > 160:
            quote = quote[:159] + "…"
        lines.append(
            f"  - {_date(occurrence.get('source_date'))} · "
            f"`{path}{_line_range(occurrence)}` · “{quote}”"
        )
    return lines


def _conflict_lines(item: dict[str, Any]) -> list[str]:
    conflicts = item.get("conflicts") or []
    if not conflicts:
        return []
    lines = ["- 冲突（未自动择一）："]
    for conflict in conflicts:
        field = conflict.get("field") or "field"
        values = conflict.get("values") or []
        rendered = "；".join(
            f"{_date(value.get('date'))} 为 “{str(value.get('value'))[:80]}”" for value in values
        )
        lines.append(f"  - `{field}`：{rendered}")
    return lines


def render_topic_inner(topic: str, items: list[dict[str, Any]]) -> str:
    """渲染自动区内容（不含 frontmatter 与标记）。"""
    title = TOPIC_FILE_NAMES.get(topic, topic)
    visible = [
        item for item in items if item.get("status") not in {"pending", "dropped"}
    ]
    open_items = [item for item in visible if item.get("status") == "open"]
    stale_items = [item for item in visible if item.get("status") == "stale"]
    resolved_items = [item for item in visible if item.get("status") == "resolved"]

    lines = [f"# {title}", ""]
    lines.append(
        f"> 共 {len(visible)} 条（open {len(open_items)} / stale {len(stale_items)} / "
        f"resolved {len(resolved_items)}）。"
    )
    lines.append("")

    def section(name: str, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        lines.extend([f"## {name}", ""])
        for item in entries:
            lines.append(f"### [{item.get('canonical_key', '')[:8]}] {item.get('title', '')}")
            lines.append("")
            lines.append(
                f"- 首次：{_date(item.get('first_seen'))} ｜ 最近：{_date(item.get('last_seen'))} "
                f"｜ 出现 {int(item.get('occurrence_count') or 0)} 次"
            )
            lines.extend(_conflict_lines(item))
            occurrences = _occurrence_lines(item)
            if occurrences:
                lines.append("- 出现记录：")
                lines.extend(occurrences)
            lines.append("")

    section("待解决", open_items)
    section("陈旧（降权，不隐藏）", stale_items)
    section("已解决", resolved_items)
    if not visible:
        lines.extend(["（暂无条目）", ""])
    return "\n".join(lines).strip("\n")


def render_topic_markdown(
    topic: str,
    items: list[dict[str, Any]],
    *,
    generated_at: str,
) -> str:
    """完整专题文件：frontmatter + auto 区（标记外内容由合并逻辑保留）。"""
    inner = render_topic_inner(topic, items)
    frontmatter = _frontmatter(
        {
            "mylife": "true",
            "kind": "topic",
            "topic": topic,
            "generated_at": generated_at,
            "generator": GENERATOR,
        }
    )
    return f"{frontmatter}\n\n{wrap_auto_region(inner)}"


__all__ = [
    "GENERATOR",
    "STATUS_LABELS",
    "render_topic_inner",
    "render_topic_markdown",
]
