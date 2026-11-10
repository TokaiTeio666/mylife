"""确定性门禁 G0–G9（ARCHITECTURE §9.1；AC-36/37/38/41）。

纯函数、零 LLM、零 IO：输入是已经装配好的 Draft / EvidenceBundle / 条目，
输出 `GateIssue` 列表。放行只由这里的 blocking 决定。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from mylife_contract.constants import (
    MAX_EVIDENCE_PER_RUN,
    SECTION_ORDINALS,
    SENTINELS,
)
from mylife_contract.review import (
    ITEM_SCHEMAS,
    SECTION_SCHEMAS,
    EvidenceItem,
    GateIssue,
    ReviewVerdict,
)
from mylife_runtime.hashing import normalize_text, quote_hash

ACTION_PATTERN = re.compile(r"^- \[ \] .+", re.MULTILINE)
MAX_EVIDENCE_SECTION = 12

#: 允许的 section 证据上限覆盖（§5.1 子表）。
SECTION_CAPS: dict[str, dict[str, int]] = {
    "daily": {key: cap for _, key, _, cap in SECTION_ORDINALS["daily"]},
    "monthly": {key: cap for _, key, _, cap in SECTION_ORDINALS["monthly"]},
    "yearly": {key: cap for _, key, _, cap in SECTION_ORDINALS["yearly"]},
}


@dataclass(frozen=True, slots=True)
class GateContext:
    """门禁输入（全部由确定性代码装配）。"""

    kind: str
    workspace_id: str
    sections: Sequence[dict[str, Any]]  # section_key/ordinal/content_md/structured/claim_ids
    claims: Sequence[dict[str, Any]]
    evidence: Sequence[EvidenceItem]
    bundle_snapshot_id: str | None
    entries: Sequence[dict[str, Any]]
    snapshot_hashes: dict[str, str] | None = None
    frozen_text: dict[str, str] | None = None
    citations: Sequence[dict[str, Any]] = ()
    coverage_gaps: Sequence[dict[str, Any]] = ()
    target_paths: Sequence[str] = ()
    parser_version: str = ""


def _issue(
    gate: str,
    code: str,
    message: str,
    *,
    severity: str = "blocking",
    section_key: str | None = None,
    claim_id: str | None = None,
    evidence_id: str | None = None,
    must_fix: str | None = None,
) -> GateIssue:
    return GateIssue(
        gate=gate,
        severity=severity,  # type: ignore[arg-type]
        code=code,
        message=message,
        section_key=section_key,
        claim_id=claim_id,
        evidence_id=evidence_id,
        must_fix=must_fix,
    )


# ---------------------------------------------------------------------------
# G0 租户归属
# ---------------------------------------------------------------------------


def gate_g0(ctx: GateContext, resolved_owners: dict[str, str]) -> list[GateIssue]:
    """`resolved_owners` 为 evidence_id → workspace_id（由 DB/RLS 查询得到）。"""
    issues: list[GateIssue] = []
    for item in ctx.evidence:
        if item.source_type not in {"document", "chunk", "review"}:
            continue
        owner = resolved_owners.get(item.evidence_id)
        if owner is not None and owner != ctx.workspace_id:
            issues.append(
                _issue(
                    "G0",
                    "TENANT_LEAK",
                    "证据不属于当前 workspace",
                    evidence_id=item.evidence_id,
                )
            )
    return issues


# ---------------------------------------------------------------------------
# G1 章节集合与 ordinal
# ---------------------------------------------------------------------------


def gate_g1(ctx: GateContext) -> list[GateIssue]:
    expected = SECTION_ORDINALS.get(ctx.kind)
    if expected is None:
        return [_issue("G1", "SECTION_SET_MISMATCH", f"未知 kind: {ctx.kind}")]
    expected_keys = [key for _, key, _, _ in expected]
    expected_ordinals = [ordinal for ordinal, _, _, _ in expected]

    actual_keys = [section.get("section_key") for section in ctx.sections]
    actual_ordinals = [section.get("ordinal") for section in ctx.sections]

    issues: list[GateIssue] = []
    if sorted(actual_keys) != sorted(expected_keys):
        missing = sorted(set(expected_keys) - set(actual_keys))
        extra = sorted(set(actual_keys) - set(expected_keys))
        issues.append(
            _issue(
                "G1",
                "SECTION_SET_MISMATCH",
                f"章节集合不符：缺少 {missing}，多出 {extra}",
            )
        )
    if actual_ordinals != expected_ordinals:
        issues.append(
            _issue(
                "G1",
                "SECTION_ORDINAL_MISMATCH",
                f"ordinal 序列应为 {expected_ordinals}，实际 {actual_ordinals}",
            )
        )
    for section in ctx.sections:
        if not (section.get("content_md") or "").strip():
            issues.append(
                _issue(
                    "G1",
                    "SECTION_EMPTY",
                    "章节正文为空",
                    section_key=section.get("section_key"),
                )
            )
    return issues


# ---------------------------------------------------------------------------
# G2 schema
# ---------------------------------------------------------------------------


def gate_g2(ctx: GateContext) -> list[GateIssue]:
    from pydantic import ValidationError

    issues: list[GateIssue] = []
    for section in ctx.sections:
        key = section.get("section_key", "")
        model = SECTION_SCHEMAS.get(ctx.kind, {}).get(key)
        if model is None:
            continue
        if section.get("sentinel_used"):
            # 哨兵章节允许 structured_json 为空对象（REND-02 裁决）。
            continue
        try:
            model.model_validate(section.get("structured") or {})
        except ValidationError as exc:
            issues.append(
                _issue(
                    "G2",
                    "SCHEMA_INVALID",
                    f"structured_json 校验失败（{exc.error_count()} 项）",
                    section_key=key,
                )
            )
    for entry in ctx.entries:
        item_type = entry.get("item_type", "")
        model = ITEM_SCHEMAS.get(item_type)
        if model is None:
            issues.append(
                _issue(
                    "G2",
                    "SCHEMA_INVALID",
                    f"未知 item_type: {item_type}",
                    section_key=entry.get("section_key"),
                )
            )
            continue
        try:
            model.model_validate(entry.get("fields") or {})
        except ValidationError as exc:
            issues.append(
                _issue(
                    "G2",
                    "SCHEMA_INVALID",
                    f"fields_json 校验失败（{exc.error_count()} 项）",
                    section_key=entry.get("section_key"),
                )
            )
    return issues


# ---------------------------------------------------------------------------
# G3a–G3d / G4
# ---------------------------------------------------------------------------


def gate_g3a(ctx: GateContext) -> list[GateIssue]:
    # 空素材日（FX-04）没有证据也没有快照，是合法输出。
    if not ctx.bundle_snapshot_id and ctx.evidence:
        return [_issue("G3a", "SNAPSHOT_MISMATCH", "缺少 bundle.snapshot_id")]
    return []


def gate_g3b(ctx: GateContext) -> list[GateIssue]:
    """源哈希不一致 → EVIDENCE_STALE；**不得**判 FABRICATION。"""
    if not ctx.snapshot_hashes:
        return []
    issues: list[GateIssue] = []
    for item in ctx.evidence:
        if item.source_type not in {"document", "chunk", "review", "task"}:
            continue
        if not item.document_content_hash:
            # DATA-MODEL §4.8 只对 document/chunk/review 强制哈希；
            # task 证据来自台账（合成行），允许无哈希。
            if item.source_type in {"document", "chunk", "review"}:
                issues.append(
                    _issue(
                        "G3b",
                        "EVIDENCE_STALE",
                        "证据缺少 document_content_hash",
                        evidence_id=item.evidence_id,
                        must_fix="重新取证并绑定快照哈希",
                    )
                )
            continue
        frozen = ctx.snapshot_hashes.get(item.source_path or "")
        if frozen is not None and frozen != item.document_content_hash:
            issues.append(
                _issue(
                    "G3b",
                    "EVIDENCE_STALE",
                    "证据哈希与冻结快照不一致",
                    evidence_id=item.evidence_id,
                    must_fix="定向补检索该文档",
                )
            )
    return issues


def gate_g3c(ctx: GateContext) -> list[GateIssue]:
    if not ctx.frozen_text:
        return []
    issues: list[GateIssue] = []
    for item in ctx.evidence:
        if item.source_type not in {"document", "chunk"}:
            continue
        text = ctx.frozen_text.get(item.source_path or "")
        if text is None:
            issues.append(
                _issue(
                    "G3c",
                    "QUOTE_NOT_FOUND",
                    "无法读取冻结原文",
                    evidence_id=item.evidence_id,
                )
            )
            continue
        window = _window(text, item.line_range)
        if normalize_text(item.quote) not in normalize_text(window):
            issues.append(
                _issue(
                    "G3c",
                    "QUOTE_NOT_FOUND",
                    "引用原文不在标注行号范围内",
                    evidence_id=item.evidence_id,
                    must_fix="校正 line_range 或 quote",
                )
            )
    return issues


def gate_g3d(ctx: GateContext) -> list[GateIssue]:
    issues: list[GateIssue] = []
    for item in ctx.evidence:
        if item.source_type == "stats":
            continue
        expected = quote_hash(ctx.parser_version, item.quote)
        if expected != item.quote_hash:
            issues.append(
                _issue(
                    "G3d",
                    "QUOTE_HASH_MISMATCH",
                    "quote_hash 复算不一致",
                    evidence_id=item.evidence_id,
                )
            )
    return issues


def gate_g4(ctx: GateContext) -> list[GateIssue]:
    issues: list[GateIssue] = []
    for entry in ctx.entries:
        if entry.get("item_type") not in {"error", "method"}:
            continue
        line_range = entry.get("line_range")
        missing = (
            not entry.get("source_date")
            or not entry.get("source_path")
            or not line_range
            or len(line_range) != 2
        )
        if missing:
            issues.append(
                _issue(
                    "G4",
                    "TRACEABILITY_MISSING",
                    "错误/方法论条目缺少 source_date + source_path + line_range",
                    section_key=entry.get("section_key"),
                    evidence_id=entry.get("evidence_id"),
                )
            )
    return issues


def _window(text: str, line_range: Any) -> str:
    if not line_range or len(line_range) != 2:
        return text
    start, end = int(line_range[0]), int(line_range[1])
    lines = text.split("\n")
    if start < 1 or end < start:
        return ""
    return "\n".join(lines[start - 1 : end])


# ---------------------------------------------------------------------------
# G5–G7
# ---------------------------------------------------------------------------


def gate_g5(ctx: GateContext) -> list[GateIssue]:
    issues: list[GateIssue] = []
    evidence_ids = {item.evidence_id for item in ctx.evidence}
    for claim in ctx.claims:
        ids = claim.get("evidence_ids") or []
        if not ids:
            issues.append(
                _issue(
                    "G5",
                    "UNSUPPORTED_CLAIM",
                    "claim 未绑定证据",
                    claim_id=claim.get("claim_id"),
                    section_key=claim.get("section_key"),
                )
            )
            continue
        for evidence_id in ids:
            if evidence_id not in evidence_ids:
                issues.append(
                    _issue(
                        "G5",
                        "UNSUPPORTED_CLAIM",
                        "claim 引用了不存在的 evidence_id",
                        claim_id=claim.get("claim_id"),
                        evidence_id=evidence_id,
                    )
                )
    return issues


def gate_g6(ctx: GateContext) -> list[GateIssue]:
    issues: list[GateIssue] = []
    targets = {"tomorrow_actions", "next_month_actions"}
    for section in ctx.sections:
        if section.get("section_key") not in targets:
            continue
        if section.get("sentinel_used"):
            continue
        structured = section.get("structured") or {}
        for item in structured.get("items", []):
            if not item.get("trigger") or not item.get("verification"):
                issues.append(
                    _issue(
                        "G6",
                        "ACTION_NOT_EXECUTABLE",
                        "行动项缺少 trigger 或 verification",
                        section_key=section.get("section_key"),
                    )
                )
        content = section.get("content_md") or ""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- [") and not ACTION_PATTERN.match(stripped):
                issues.append(
                    _issue(
                        "G6",
                        "ACTION_NOT_EXECUTABLE",
                        "行动项必须是 `- [ ] ` 开头",
                        section_key=section.get("section_key"),
                    )
                )
    return issues


def gate_g7(ctx: GateContext) -> list[GateIssue]:
    issues: list[GateIssue] = []
    for section in ctx.sections:
        key = section.get("section_key", "")
        sentinel = SENTINELS.get(key)
        content = section.get("content_md") or ""
        used = bool(section.get("sentinel_used"))
        if used and sentinel and content.strip() != sentinel:
            issues.append(
                _issue(
                    "G7",
                    "SENTINEL_VIOLATION",
                    "哨兵章节必须逐字等于允许文案",
                    section_key=key,
                )
            )
        if not used and sentinel and sentinel in content:
            issues.append(
                _issue(
                    "G7",
                    "SENTINEL_VIOLATION",
                    "有证据章节不得出现哨兵文案",
                    section_key=key,
                )
            )
    return issues


# ---------------------------------------------------------------------------
# G8 / G9
# ---------------------------------------------------------------------------


def gate_g8(ctx: GateContext) -> list[GateIssue]:
    return [
        _issue(
            "G8",
            "COVERAGE_GAP",
            f"覆盖缺口：{gap.get('date') or gap.get('period_key')}",
            severity="warning",
            section_key=gap.get("section_key"),
        )
        for gap in ctx.coverage_gaps
    ]


ALLOWED_PATH_PREFIXES = ("每日复盘/", "每月复盘/", "年度复盘/", "专题/")
ALLOWED_EXACT = {"索引.md"}


def gate_g9(ctx: GateContext) -> list[GateIssue]:
    issues: list[GateIssue] = []
    for path in ctx.target_paths:
        normalized = normalize_text(path).replace(" ", " ")
        if ".." in normalized.split("/") or normalized.startswith("/"):
            issues.append(
                _issue("G9", "PATH_NOT_ALLOWED", f"输出路径越界：{path}")
            )
            continue
        if normalized in ALLOWED_EXACT:
            continue
        if not any(normalized.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES):
            issues.append(
                _issue("G9", "PATH_NOT_ALLOWED", f"输出路径不在白名单：{path}")
            )
    return issues


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def evidence_cap_issues(ctx: GateContext) -> list[GateIssue]:
    issues: list[GateIssue] = []
    if len(ctx.evidence) > MAX_EVIDENCE_PER_RUN:
        issues.append(
            _issue(
                "G1",
                "EVIDENCE_OVERFLOW",
                f"证据总数 {len(ctx.evidence)} 超过全局上限 {MAX_EVIDENCE_PER_RUN}",
            )
        )
    return issues


def evaluate_gates(
    ctx: GateContext,
    *,
    resolved_owners: dict[str, str] | None = None,
) -> ReviewVerdict:
    """按固定顺序执行 G0–G9，返回 verdict（不含 revision 决策）。"""
    blocking: list[GateIssue] = []
    warnings: list[GateIssue] = []

    blocking.extend(gate_g0(ctx, resolved_owners or {}))
    blocking.extend(gate_g1(ctx))
    blocking.extend(gate_g2(ctx))
    blocking.extend(gate_g3a(ctx))
    blocking.extend(gate_g3b(ctx))
    blocking.extend(gate_g3c(ctx))
    blocking.extend(gate_g3d(ctx))
    blocking.extend(gate_g4(ctx))
    blocking.extend(gate_g5(ctx))
    blocking.extend(gate_g6(ctx))
    blocking.extend(gate_g7(ctx))
    warnings.extend(gate_g8(ctx))
    blocking.extend(gate_g9(ctx))
    blocking.extend(evidence_cap_issues(ctx))

    return ReviewVerdict(
        decision="APPROVE" if not blocking else "REVISE",
        blocking=blocking,
        warnings=warnings,
        revision_round=0,
        fingerprint=fingerprint(blocking),
    )


def fingerprint(issues: Iterable[GateIssue]) -> str:
    rows = sorted(
        "|".join(
            [
                issue.gate,
                issue.code,
                issue.section_key or "",
                issue.claim_id or "",
                issue.evidence_id or "",
                issue.must_fix or "",
            ]
        )
        for issue in issues
    )
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def has_abort(issues: Sequence[GateIssue]) -> str | None:
    """返回 `tenant_leak` / `fabrication`；否则 None。"""
    for issue in issues:
        if issue.code == "TENANT_LEAK":
            return "tenant_leak"
        if issue.code == "FABRICATION":
            return "fabrication"
    return None
