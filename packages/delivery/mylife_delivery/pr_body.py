"""PR 正文六段（ARCHITECTURE §8.2；PHASE4-SPEC §4.1）。

顺序固定：30 秒结论 / 章节自检 / 证据前 10 条 / 变更文件 / 风险与回滚 / GateResult。
`degraded=true` 时正文首行为 `degraded: <reason>`（反目标 D4）。
"""

from __future__ import annotations

from typing import Any

from mylife_contract.constants import PR_BODY_SECTIONS

KIND_LABELS = {"daily": "每日复盘", "monthly": "每月复盘", "yearly": "年度复盘"}


def _one_line_summary(sections: list[dict[str, Any]]) -> str:
    for section in sections:
        if section.get("section_key") == "one_line_summary":
            text = (section.get("content_md") or "").strip()
            if text:
                return text.splitlines()[0][:500]
    return "本次复盘已完成。"


def _section_check(sections: list[dict[str, Any]]) -> list[str]:
    lines = []
    for section in sorted(sections, key=lambda item: int(item.get("ordinal") or 0)):
        key = section.get("section_key", "")
        title = section.get("title") or key
        filled = bool((section.get("content_md") or "").strip())
        lines.append(f"- [{'x' if filled else ' '}] {int(section.get('ordinal') or 0)}. {title}")
    return lines


def _evidence_lines(citations: list[dict[str, Any]]) -> list[str]:
    lines = []
    for citation in citations[:10]:
        path = citation.get("source_path") or ""
        line_range = citation.get("line_range")
        suffix = ""
        if line_range and line_range[0] is not None:
            suffix = f"#L{line_range[0]}-L{line_range[1]}"
        quote = (citation.get("quote") or "").replace("\n", " ").strip()
        if len(quote) > 120:
            quote = quote[:119] + "…"
        lines.append(f"- `{path}{suffix}` · “{quote}”")
    return lines or ["- （无引用）"]


def build_pr_body(
    *,
    kind: str,
    period_key: str,
    version: int,
    review_id: str,
    run_id: str,
    sections: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    verdict: dict[str, Any] | None,
    degraded_reasons: list[str],
) -> str:
    label = KIND_LABELS.get(kind, kind)
    blocks: list[str] = []
    if degraded_reasons:
        blocks.append(f"degraded: {', '.join(degraded_reasons)}")

    blocks.append(f"## {PR_BODY_SECTIONS[0]}")
    blocks.append(_one_line_summary(sections))

    blocks.append(f"## {PR_BODY_SECTIONS[1]}")
    blocks.extend(_section_check(sections))

    blocks.append(f"## {PR_BODY_SECTIONS[2]}")
    blocks.extend(_evidence_lines(citations))

    blocks.append(f"## {PR_BODY_SECTIONS[3]}")
    for target in targets:
        blocks.append(f"- `{target.get('path')}`（{target.get('mode')}）")

    blocks.append(f"## {PR_BODY_SECTIONS[4]}")
    blocks.append(
        f"- 回滚：`POST /reviews/{review_id}/rollback "
        f'{{"version": {max(1, version - 1)}, "confirm": true}}`'
    )
    blocks.append("- 冲突：保留分支供人工处理，不 force push。")

    blocks.append(f"## {PR_BODY_SECTIONS[5]}")
    if verdict:
        decision = verdict.get("decision") or "UNKNOWN"
        issues = verdict.get("issues") or []
        blocking = sum(1 for issue in issues if issue.get("severity") == "blocking")
        warnings = sum(1 for issue in issues if issue.get("severity") == "warning")
        blocks.append(f"{decision} ({blocking} blocking, {warnings} warnings)")
    else:
        blocks.append("APPROVE (0 blocking, 0 warnings)")

    blocks.append("")
    blocks.append(f"Run-Id: {run_id}")
    blocks.append(f"Review-Id: {review_id}")
    blocks.append(f"Version: {version}")
    blocks.append(f"Kind: {label} {period_key}")
    return "\n".join(blocks) + "\n"


__all__ = ["build_pr_body"]
