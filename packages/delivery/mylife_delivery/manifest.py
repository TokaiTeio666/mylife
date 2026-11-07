"""交付清单构建（API-CONTRACTS §4.8；ARCHITECTURE §8.2）。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from mylife_contract.constants import INDEX_PATH, REVIEW_BRANCH_PREFIX, TOPIC_FILE_NAMES
from mylife_contract.delivery import (
    DeliveryManifest,
    DeliveryTarget,
    PullRequestSpec,
    ReviewRowRef,
)

KIND_LABELS = {"daily": "每日复盘", "weekly": "每周复盘", "yearly": "年度复盘"}


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def branch_name(kind: str, period_key: str) -> str:
    return f"{REVIEW_BRANCH_PREFIX}/{kind}/{period_key}"


def commit_message(
    *,
    kind: str,
    period_key: str,
    version: int,
    run_id: str,
    review_id: str,
    workspace_id: str,
) -> str:
    label = KIND_LABELS.get(kind, kind)
    return "\n".join(
        [
            f"chore(review): 生成 {period_key} {label} (v{version})",
            "",
            f"Run-Id: {run_id}",
            f"Review-Id: {review_id}",
            f"Version: {version}",
            f"Workspace: {workspace_id}",
        ]
    )


def pr_title(kind: str, period_key: str, version: int) -> str:
    label = KIND_LABELS.get(kind, kind)
    return f"[复盘] {period_key} {label} (v{version})"


def topic_target_paths(reviews_root: str) -> dict[str, str]:
    """返回相对 `reviews_root` 的专题路径（API-CONTRACTS §4.8）。"""
    return {slug: f"专题/{name}.md" for slug, name in TOPIC_FILE_NAMES.items()}


def index_target_path(reviews_root: str) -> str:
    """返回相对 `reviews_root` 的索引路径。"""
    return INDEX_PATH


def build_manifest(
    *,
    kind: str,
    period_key: str,
    version: int,
    review_id: str,
    run_id: str,
    workspace_id: str,
    reviews_root: str,
    review_path: str,
    review_content: str,
    topic_files: dict[str, str],
    index_content: str,
    pr_body: str,
    base_branch: str,
    auto_merge: bool,
    degraded: bool,
    degraded_reasons: list[str],
) -> DeliveryManifest:
    targets: list[DeliveryTarget] = [
        DeliveryTarget(
            kind="git",
            path=review_path,
            content_hash=content_hash(review_content),
            mode="update_allowed",
        )
    ]
    for slug in ("errors", "methods", "actions"):
        path = topic_target_paths(reviews_root)[slug]
        targets.append(
            DeliveryTarget(
                kind="git",
                path=path,
                content_hash=content_hash(topic_files.get(slug, "")),
                mode="update_allowed",
            )
        )
    targets.append(
        DeliveryTarget(
            kind="git",
            path=index_target_path(reviews_root),
            content_hash=content_hash(index_content),
            mode="update_allowed",
        )
    )

    pr = PullRequestSpec(
        title=pr_title(kind, period_key, version),
        body_md=pr_body,
        base_branch=base_branch,
        head_branch=branch_name(kind, period_key),
        draft=False,
        auto_merge=auto_merge,
    )
    return DeliveryManifest(
        branch=pr.head_branch,
        targets=targets,
        commit_message=commit_message(
            kind=kind,
            period_key=period_key,
            version=version,
            run_id=run_id,
            review_id=review_id,
            workspace_id=workspace_id,
        ),
        pr=pr,
        review_row=ReviewRowRef(
            review_id=review_id,  # type: ignore[arg-type]
            version=version,
            content_hash=content_hash(review_content),
            git_path=review_path,
        ),
    )


def manifest_to_dict(manifest: DeliveryManifest) -> dict[str, Any]:
    return manifest.model_dump(mode="json")


def generated_at_iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = [
    "branch_name",
    "build_manifest",
    "commit_message",
    "content_hash",
    "generated_at_iso",
    "index_target_path",
    "manifest_to_dict",
    "pr_title",
    "topic_target_paths",
]
