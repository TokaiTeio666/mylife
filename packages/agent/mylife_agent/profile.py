"""`review-profile.yml` 加载与启动时 fail-fast 校验（ARCHITECTURE §16.3/§16.9）。

profile 只是 §5.1 常量的镜像，**不是语义源**。启动时按 kind 逐项比对，
不一致直接抛错拒绝启动，避免契约双写漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from mylife_contract.constants import (
    REVISION_LIMIT,
    SECTION_ORDINALS,
    SENTINELS,
)


def _default_profile_path() -> Path:
    """源码树在 `packages/agent/review-profile.yml`，安装后随包分发。"""
    here = Path(__file__).resolve()
    for candidate in (here.parent / "review-profile.yml", here.parents[1] / "review-profile.yml"):
        if candidate.exists():
            return candidate
    return here.parents[1] / "review-profile.yml"


DEFAULT_PROFILE_PATH = _default_profile_path()


class StartupValidationError(RuntimeError):
    """profile 与 §5.1 常量不一致。"""


@dataclass(frozen=True, slots=True)
class SectionSpec:
    section_key: str
    ordinal: int
    title: str
    required_fields: list[str]
    sentinel: str
    max_evidence: int


@dataclass(frozen=True, slots=True)
class ReviewProfile:
    sections: dict[str, list[SectionSpec]]
    record_types: list[str]
    write_modes: dict[str, str]
    sentinels: dict[str, str]
    context_rules: dict[str, Any]


def load_review_profile(path: Path | str | None = None) -> ReviewProfile:
    target = Path(path) if path is not None else DEFAULT_PROFILE_PATH
    if not target.exists():
        raise StartupValidationError(f"review-profile.yml 不存在: {target}")
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise StartupValidationError("review-profile.yml 必须是映射")

    sections: dict[str, list[SectionSpec]] = {}
    for kind, rows in (payload.get("sections") or {}).items():
        specs: list[SectionSpec] = []
        for row in rows or []:
            specs.append(
                SectionSpec(
                    section_key=str(row["section_key"]),
                    ordinal=int(row["ordinal"]),
                    title=str(row["title"]),
                    required_fields=[str(item) for item in row.get("required_fields", [])],
                    sentinel=str(row["sentinel"]),
                    max_evidence=int(row["max_evidence"]),
                )
            )
        sections[str(kind)] = specs

    return ReviewProfile(
        sections=sections,
        record_types=[str(item) for item in payload.get("record_types", [])],
        write_modes={str(k): str(v) for k, v in (payload.get("write_modes") or {}).items()},
        sentinels={str(k): str(v) for k, v in (payload.get("sentinels") or {}).items()},
        context_rules=dict(payload.get("context_rules") or {}),
    )


def validate_review_profile(profile: ReviewProfile | None = None) -> ReviewProfile:
    """按 kind 逐项比对 §5.1 常量；任一不等 → StartupValidationError。"""
    profile = profile or load_review_profile()
    errors: list[str] = []

    for kind, constants in SECTION_ORDINALS.items():
        specs = profile.sections.get(kind)
        if not specs:
            errors.append(f"{kind}: profile 缺少 sections")
            continue
        expected_keys = [key for _, key, _, _ in constants]
        actual_keys = [spec.section_key for spec in specs]
        if actual_keys != expected_keys:
            errors.append(f"{kind}: section_key 序列 {actual_keys} != {expected_keys}")
        expected_ordinals = [ordinal for ordinal, _, _, _ in constants]
        actual_ordinals = [spec.ordinal for spec in specs]
        if actual_ordinals != expected_ordinals:
            errors.append(f"{kind}: ordinal 序列 {actual_ordinals} != {expected_ordinals}")
        for spec, (ordinal, key, title, cap) in zip(specs, constants, strict=False):
            if spec.title != title:
                errors.append(f"{kind}/{key}: title {spec.title!r} != {title!r}")
            if spec.max_evidence != cap:
                errors.append(
                    f"{kind}/{key}: max_evidence {spec.max_evidence} != {cap}"
                )
            if spec.sentinel != SENTINELS.get(key):
                errors.append(f"{kind}/{key}: sentinel 与 SENTINELS 不一致")

    for key, value in SENTINELS.items():
        if key in {"chat_no_evidence"}:
            continue
        if profile.sentinels.get(key) != value:
            errors.append(f"sentinels.{key} 与常量不一致")

    if set(profile.record_types) != {"error", "method", "action"}:
        errors.append(f"record_types 必须为 error/method/action，实际 {profile.record_types}")

    if set(REVISION_LIMIT) - set(SECTION_ORDINALS):
        errors.append("REVISION_LIMIT 含未知 kind")

    if errors:
        raise StartupValidationError("review-profile 校验失败:\n" + "\n".join(errors))
    return profile
