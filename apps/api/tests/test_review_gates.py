"""确定性门禁 G0–G9 与 rubric（ARCHITECTURE §9.1/§9.2；AC-36/41）。"""

from __future__ import annotations

from datetime import date

import pytest
from mylife_agent.entry_materializer import materialize_entries
from mylife_agent.gates import GateContext, evaluate_gates, gate_g3b, has_abort
from mylife_agent.profile import (
    StartupValidationError,
    load_review_profile,
    validate_review_profile,
)
from mylife_agent.reviewer import decide, run_review
from mylife_contract.constants import SENTINELS
from mylife_contract.review import EvidenceItem, ReviewVerdict
from mylife_runtime.hashing import quote_hash

PARSER = "mylife-parser-1:test"
QUOTE = "缓存穿透是指查询不存在的数据。"


def _evidence(**overrides) -> EvidenceItem:
    payload = {
        "evidence_id": "e1",
        "source_type": "chunk",
        "document_id": "0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
        "chunk_id": "0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c",
        "source_path": "notes/redis.md",
        "source_date": date(2026, 10, 27),
        "occurred_on": date(2026, 10, 27),
        "document_content_hash": "a" * 64,
        "heading": "Redis",
        "line_range": (1, 1),
        "quote": QUOTE,
        "quote_hash": quote_hash(PARSER, QUOTE),
        "tags": ["redis"],
    }
    payload.update(overrides)
    return EvidenceItem.model_validate(payload)


def _sections(kind: str, *, sentinel_all: bool = False) -> list[dict]:
    from mylife_contract.constants import SECTION_ORDINALS

    rows = []
    for ordinal, key, _title, _cap in SECTION_ORDINALS[kind]:
        rows.append(
            {
                "section_key": key,
                "ordinal": ordinal,
                "content_md": SENTINELS[key] if sentinel_all else f"{key} 正文",
                "structured": {},
                "claim_ids": [],
                "sentinel_used": sentinel_all,
            }
        )
    return rows


def _ctx(**overrides) -> GateContext:
    payload = {
        "kind": "daily",
        "workspace_id": "ws-1",
        "sections": _sections("daily"),
        "claims": [],
        "evidence": [],
        "bundle_snapshot_id": "snap-1",
        "entries": [],
        "snapshot_hashes": {},
        "frozen_text": {},
        "target_paths": ("每日复盘/2026/2026-10-27.md",),
        "parser_version": PARSER,
    }
    payload.update(overrides)
    return GateContext(**payload)


def test_g1_rejects_missing_and_extra_sections():
    sections = _sections("daily")
    verdict = evaluate_gates(_ctx(sections=sections[:-1]))
    assert any(
        issue.gate == "G1" and issue.code == "SECTION_SET_MISMATCH"
        for issue in verdict.blocking
    )

    duplicated = sections + [dict(sections[0])]
    verdict = evaluate_gates(_ctx(sections=duplicated))
    assert any(issue.code == "SECTION_ORDINAL_MISMATCH" for issue in verdict.blocking)


def test_g1_rejects_empty_section():
    sections = _sections("daily")
    sections[0]["content_md"] = "   "
    verdict = evaluate_gates(_ctx(sections=sections))
    assert any(issue.code == "SECTION_EMPTY" for issue in verdict.blocking)


def test_g0_flags_cross_tenant_evidence():
    verdict = evaluate_gates(
        _ctx(evidence=[_evidence()]), resolved_owners={"e1": "ws-other"}
    )
    assert has_abort(verdict.blocking) == "tenant_leak"


def test_g3b_hash_mismatch_is_stale_not_fabrication():
    verdict = evaluate_gates(
        _ctx(
            evidence=[_evidence()],
            snapshot_hashes={"notes/redis.md": "b" * 64},
        )
    )
    codes = {issue.code for issue in verdict.blocking}
    assert "EVIDENCE_STALE" in codes
    assert "FABRICATION" not in codes
    assert has_abort(verdict.blocking) is None


def test_g3b_missing_hash_is_stale():
    ctx = _ctx(
        evidence=[_evidence(document_content_hash=None)],
        snapshot_hashes={"notes/redis.md": "a" * 64},
    )
    issues = gate_g3b(ctx)
    assert [issue.code for issue in issues] == ["EVIDENCE_STALE"]


def test_g3c_quote_must_be_in_line_range():
    ctx = _ctx(
        evidence=[_evidence(line_range=(2, 2))],
        frozen_text={"notes/redis.md": "第一行\n第二行\n"},
    )
    verdict = evaluate_gates(ctx)
    assert any(issue.code == "QUOTE_NOT_FOUND" for issue in verdict.blocking)


def test_g3d_quote_hash_mismatch():
    verdict = evaluate_gates(
        _ctx(evidence=[_evidence(quote_hash="c" * 64)], frozen_text={})
    )
    assert any(issue.code == "QUOTE_HASH_MISMATCH" for issue in verdict.blocking)


def test_g4_requires_traceability_for_errors_and_methods():
    entries = [
        {"section_key": "mistakes", "item_type": "error", "fields": {}, "source_path": "a.md"},
    ]
    verdict = evaluate_gates(_ctx(entries=entries))
    assert any(issue.code == "TRACEABILITY_MISSING" for issue in verdict.blocking)

    ok_entries = [
        {
            "section_key": "mistakes",
            "item_type": "error",
            "fields": {},
            "source_path": "a.md",
            "source_date": date(2026, 10, 27),
            "line_range": [3, 4],
        }
    ]
    verdict = evaluate_gates(_ctx(entries=ok_entries))
    assert not any(issue.code == "TRACEABILITY_MISSING" for issue in verdict.blocking)


def test_g5_claim_requires_existing_evidence():
    claims = [{"claim_id": "c1", "section_key": "today_tasks", "evidence_ids": ["e9"]}]
    verdict = evaluate_gates(_ctx(claims=claims, evidence=[_evidence()]))
    assert any(issue.code == "UNSUPPORTED_CLAIM" for issue in verdict.blocking)


def test_g6_action_requires_trigger_and_verification():
    sections = _sections("daily")
    for section in sections:
        if section["section_key"] == "tomorrow_actions":
            section["structured"] = {
                "items": [{"text": "读论文", "trigger": "", "verification": ""}]
            }
    verdict = evaluate_gates(_ctx(sections=sections))
    assert any(issue.code == "ACTION_NOT_EXECUTABLE" for issue in verdict.blocking)


def test_g7_sentinel_must_be_exact():
    sections = _sections("daily", sentinel_all=True)
    sections[0]["content_md"] = "今日待办：无"
    verdict = evaluate_gates(_ctx(sections=sections))
    assert any(issue.code == "SENTINEL_VIOLATION" for issue in verdict.blocking)

    # 全哨兵日：G1/G7 均通过
    verdict = evaluate_gates(_ctx(sections=_sections("daily", sentinel_all=True)))
    assert not verdict.blocking


def test_g8_coverage_gap_is_warning_only():
    verdict = evaluate_gates(
        _ctx(kind="monthly", sections=_sections("monthly"), coverage_gaps=[{"date": "2026-10-24"}])
    )
    assert any(issue.gate == "G8" and issue.severity == "warning" for issue in verdict.warnings)
    assert not any(issue.gate == "G8" for issue in verdict.blocking)


def test_g9_path_whitelist():
    verdict = evaluate_gates(_ctx(target_paths=("../../etc/passwd",)))
    assert any(issue.code == "PATH_NOT_ALLOWED" for issue in verdict.blocking)


def test_monthly_and_yearly_contracts():
    for kind in ("monthly", "yearly"):
        verdict = evaluate_gates(
            _ctx(kind=kind, sections=_sections(kind, sentinel_all=True), target_paths=())
        )
        assert not verdict.blocking, (kind, verdict.blocking)


def test_evidence_global_cap():
    evidence = [_evidence(evidence_id=f"e{i}") for i in range(81)]
    verdict = evaluate_gates(_ctx(evidence=evidence))
    assert any(issue.code == "EVIDENCE_OVERFLOW" for issue in verdict.blocking)


def test_revision_convergence_requires_three_conditions():
    verdict = ReviewVerdict(
        decision="REVISE",
        blocking=[{"gate": "G1", "severity": "blocking", "code": "X", "message": "m"}],
        revision_round=0,
        fingerprint="fp1",
    )
    # 指纹未变 → 不允许继续修订
    unchanged = decide(
        verdict, kind="daily", revision_round=0, previous_fingerprint="fp1"
    )
    assert unchanged.next_state == "human_gate"
    # 轮次耗尽 → 人工
    exhausted = decide(
        verdict, kind="daily", revision_round=2, previous_fingerprint="fp0"
    )
    assert exhausted.next_state == "human_gate"
    # 三条件满足 → 修订
    converging = decide(
        verdict, kind="daily", revision_round=0, previous_fingerprint="fp0"
    )
    assert converging.next_state == "revising"


def test_abort_bypasses_revision():
    verdict = ReviewVerdict(
        decision="REVISE",
        blocking=[{"gate": "G0", "severity": "blocking", "code": "TENANT_LEAK", "message": "m"}],
        revision_round=0,
        fingerprint="fp",
    )
    assert decide(verdict, kind="daily", revision_round=0).next_state == "security_abort"


def test_fact_consistency_is_warning_only():
    result = run_review(_ctx(sections=_sections("daily", sentinel_all=True)), fact_consistency=0.5)
    assert result.next_state == "delivering"
    assert any(issue.gate == "R4" for issue in result.verdict.warnings)


def test_materializer_is_idempotent_and_binds_evidence():
    sections = [
        {
            "section_key": "mistakes",
            "structured": {
                "items": [
                    {
                        "event": "漏备份",
                        "root_cause": "忘记",
                        "cost": "1h",
                        "correct_action": "加提醒",
                        "recurrence_trigger": "周五",
                        "evidence_id": "e1",
                    },
                    {
                        "event": "漏备份",
                        "root_cause": "忘记",
                        "cost": "1h",
                        "correct_action": "加提醒",
                        "recurrence_trigger": "周五",
                    },
                ]
            },
        }
    ]
    evidence_by_path = {
        "e1": {
            "evidence_id": "e1",
            "source_path": "notes/a.md",
            "line_range": [3, 4],
            "source_date": date(2026, 10, 27),
        }
    }
    entries = materialize_entries(sections, evidence_by_path=evidence_by_path)
    assert len(entries) == 1  # 同 canonical_key 幂等
    assert entries[0].item_type == "error"
    assert entries[0].source_path == "notes/a.md"
    assert entries[0].root_cause_fingerprint


def test_review_profile_matches_constants():
    profile = load_review_profile()
    validate_review_profile(profile)
    assert set(profile.sections) == {"daily", "monthly", "yearly"}


def test_review_profile_fails_fast_on_drift(tmp_path):
    source = load_review_profile()
    broken = dict(source.sections)
    broken["daily"] = list(source.sections["daily"])
    broken["daily"][0] = type(broken["daily"][0])(
        section_key="today_tasks",
        ordinal=1,
        title="今日待办",
        required_fields=[],
        sentinel="错误的哨兵",
        max_evidence=12,
    )
    profile = type(source)(
        sections=broken,
        record_types=source.record_types,
        write_modes=source.write_modes,
        sentinels=source.sentinels,
        context_rules=source.context_rules,
    )
    with pytest.raises(StartupValidationError):
        validate_review_profile(profile)
