"""mylife 交付层（ARCHITECTURE §8；DIRECTORY-CONSTRAINTS §2）。

职责：Markdown 渲染、auto 区合并、专题/索引页、PR 正文、Git 操作与
GitHub/GitLab provider。**不含业务语义**（不判断“这是不是错误”）。
"""

from mylife_delivery.git_ops import GitDeliveryError, GitRepo, validate_relative_path
from mylife_delivery.index_page import render_index_markdown
from mylife_delivery.markers import (
    ManualDetected,
    MarkerBroken,
    auto_region_hash,
    extract_auto_region,
    merge_auto_region,
    wrap_auto_region,
)
from mylife_delivery.object_store import bundle_zip_path, write_bundle
from mylife_delivery.pr_body import build_pr_body
from mylife_delivery.renderer import (
    GENERATOR,
    RenderedReview,
    render_review_markdown,
    review_path,
    topic_path,
)
from mylife_delivery.topics import render_topic_inner, render_topic_markdown

__all__ = [
    "GENERATOR",
    "GitDeliveryError",
    "GitRepo",
    "ManualDetected",
    "MarkerBroken",
    "RenderedReview",
    "auto_region_hash",
    "build_pr_body",
    "bundle_zip_path",
    "extract_auto_region",
    "merge_auto_region",
    "render_index_markdown",
    "render_review_markdown",
    "render_topic_inner",
    "render_topic_markdown",
    "review_path",
    "topic_path",
    "validate_relative_path",
    "wrap_auto_region",
    "write_bundle",
]
