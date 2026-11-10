"""`索引.md` 渲染（DIRECTORY-CONSTRAINTS §4；PHASE4-SPEC §5.5）。

排序固定：日报/月报/年报各取最新一条；专题固定 errors → methods → actions。
"""

from __future__ import annotations

from typing import Any

from mylife_contract.constants import TOPIC_FILE_NAMES

from mylife_delivery.markers import wrap_auto_region

GENERATOR = "mylife/0.4"


def _frontmatter(generated_at: str) -> str:
    return "\n".join(
        [
            "---",
            "mylife: true",
            "kind: index",
            f"generated_at: {generated_at}",
            f"generator: {GENERATOR}",
            "---",
        ]
    )


def render_index_inner(
    latest: dict[str, dict[str, Any] | None],
    topic_counts: dict[str, int],
) -> str:
    lines = ["# 复盘索引", ""]
    labels = {"daily": "最新日报", "monthly": "最新月报", "yearly": "最新年报"}
    for kind in ("daily", "monthly", "yearly"):
        row = latest.get(kind)
        if row is None:
            lines.append(f"- {labels[kind]}：暂无")
        else:
            lines.append(
                f"- {labels[kind]}：{row.get('period_key')}（v{int(row.get('version') or 1)}）"
            )
    lines.extend(["", "## 专题"])
    for slug in ("errors", "methods", "actions"):
        name = TOPIC_FILE_NAMES[slug]
        lines.append(f"- [{name}](专题/{name}.md)：open {int(topic_counts.get(slug, 0))}")
    return "\n".join(lines)


def render_index_markdown(
    latest: dict[str, dict[str, Any] | None],
    topic_counts: dict[str, int],
    *,
    generated_at: str,
) -> str:
    return f"{_frontmatter(generated_at)}\n\n{wrap_auto_region(render_index_inner(latest, topic_counts))}"


__all__ = ["GENERATOR", "render_index_inner", "render_index_markdown"]
