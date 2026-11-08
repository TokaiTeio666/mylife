"""交付包单元测试：标记、专题、索引、PR 正文、Git 操作、对象存储。"""

from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from mylife_delivery.git_ops import GitDeliveryError, GitRepo, validate_relative_path
from mylife_delivery.index_page import render_index_markdown
from mylife_delivery.manifest import branch_name, commit_message
from mylife_delivery.markers import (
    ManualDetected,
    MarkerBroken,
    auto_region_hash,
    merge_auto_region,
    wrap_auto_region,
)
from mylife_delivery.object_store import write_bundle
from mylife_delivery.pr_body import build_pr_body
from mylife_delivery.topics import render_topic_inner, render_topic_markdown


def test_auto_region_merge_preserves_human_text():
    inner = "自动内容"
    existing = "前言\n\n" + wrap_auto_region(inner) + "\n## 人工段落\n保留我\n"
    merged, digest = merge_auto_region(
        existing, "新内容", previous_auto_hash=auto_region_hash(inner)
    )
    assert "新内容" in merged
    assert "自动内容" not in merged
    assert "## 人工段落\n保留我" in merged
    assert digest == auto_region_hash("新内容")


def test_auto_region_missing_marker_is_broken():
    with pytest.raises(MarkerBroken):
        merge_auto_region("没有标记的正文", "内容")


def test_auto_region_duplicate_marker_is_broken():
    text = wrap_auto_region("a") + wrap_auto_region("b")
    with pytest.raises(MarkerBroken):
        merge_auto_region(text, "c")


def test_auto_region_human_edit_detected():
    existing = wrap_auto_region("人工改过的内容")
    with pytest.raises(ManualDetected):
        merge_auto_region(existing, "新内容", previous_auto_hash=auto_region_hash("原始"))


def test_topic_render_excludes_pending_and_dropped_and_marks_conflict():
    items = [
        {
            "canonical_key": "ck-open-1",
            "title": "缓存穿透",
            "status": "open",
            "first_seen": "2026-10-25",
            "last_seen": "2026-10-27",
            "occurrence_count": 2,
            "conflicts": [
                {
                    "field": "cost",
                    "values": [
                        {"date": "2026-10-25", "value": "2h"},
                        {"date": "2026-10-26", "value": "半天"},
                    ],
                }
            ],
            "occurrences": [
                {
                    "source_date": "2026-10-25",
                    "source_path": "notes/a.md",
                    "line_range": [12, 13],
                    "quote": "布隆过滤器没加",
                }
            ],
        },
        {
            "canonical_key": "ck-pending",
            "title": "待确认",
            "status": "pending",
            "first_seen": "2026-10-27",
            "last_seen": "2026-10-27",
            "occurrence_count": 1,
            "occurrences": [],
        },
        {
            "canonical_key": "ck-dropped",
            "title": "已丢弃",
            "status": "dropped",
            "first_seen": "2026-10-27",
            "last_seen": "2026-10-27",
            "occurrence_count": 1,
            "occurrences": [],
        },
    ]
    text = render_topic_inner("errors", items)
    assert "缓存穿透" in text
    assert "待确认" not in text
    assert "已丢弃" not in text
    assert "冲突（未自动择一）" in text
    assert "`cost`" in text
    assert "notes/a.md#L12-L13" in text
    # 确定性
    assert render_topic_inner("errors", items) == text


def test_topic_markdown_has_frontmatter_and_marker():
    text = render_topic_markdown("methods", [], generated_at="2026-11-07T00:00:00Z")
    assert "kind: topic" in text
    assert "<!-- mylife:auto:start -->" in text
    assert "暂无条目" in text


def test_index_page_fixed_order():
    text = render_index_markdown(
        {
            "daily": {"period_key": "2026-10-27", "version": 2},
            "weekly": None,
            "yearly": {"period_key": "2026", "version": 1},
        },
        {"errors": 3, "methods": 2, "actions": 1},
        generated_at="2026-11-07T00:00:00Z",
    )
    assert "最新日报：2026-10-27（v2）" in text
    assert "最新周报：暂无" in text
    assert text.index("错误清单") < text.index("正确方法论") < text.index("行动清单")


def test_pr_body_six_sections_and_degraded_first_line():
    body = build_pr_body(
        kind="daily",
        period_key="2026-10-27",
        version=2,
        review_id="r1",
        run_id="run1",
        sections=[
            {"ordinal": 1, "section_key": "today_tasks", "title": "今日待办", "content_md": "x"},
            {"ordinal": 7, "section_key": "one_line_summary", "title": "一句话总结",
             "content_md": "今天完成了缓存方案"},
        ],
        citations=[],
        targets=[{"path": "每日复盘/2026/2026-10-27.md", "mode": "update_allowed"}],
        verdict={"decision": "APPROVE", "issues": []},
        degraded_reasons=["missing"],
    )
    assert body.startswith("degraded: missing")
    for heading in (
        "## 30 秒结论",
        "## 章节自检",
        "## 证据前 10 条",
        "## 变更文件",
        "## 风险与回滚",
        "## GateResult",
    ):
        assert heading in body
    assert "今天完成了缓存方案" in body


def test_branch_and_commit_message():
    assert branch_name("daily", "2026-10-27") == "review/daily/2026-10-27"
    message = commit_message(
        kind="daily",
        period_key="2026-10-27",
        version=3,
        run_id="run",
        review_id="review",
        workspace_id="ws",
    )
    for trailer in ("Run-Id: run", "Review-Id: review", "Version: 3", "Workspace: ws"):
        assert trailer in message


def test_validate_relative_path_rejects_escapes():
    for bad in ("../x.md", "/abs.md", "a\\b.md", "a//b.md", ".hidden/x.md"):
        with pytest.raises(GitDeliveryError):
            validate_relative_path(bad)
    assert validate_relative_path("每日复盘/2026/2026-10-27.md")


def _git(*args: str, cwd: Path) -> str:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    completed = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _make_bare_repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    (work / "README.md").write_text("# repo\n", encoding="utf-8")
    _git("add", ".", cwd=work)
    _git("commit", "-m", "init", cwd=work)
    remote = tmp_path / "remote.git"
    _git("clone", "--bare", str(work), str(remote), cwd=tmp_path)
    return remote


def test_git_repo_commit_and_push_local_bare(tmp_path):
    remote = _make_bare_repo(tmp_path)
    repo = GitRepo(root=tmp_path / "checkout", allow_local=True)
    repo.clone(str(remote), "main")
    repo.ensure_branch("review/daily/2026-10-27", "main")
    repo.write_files({"每日复盘/2026/2026-10-27.md": "# 日报\n"})
    sha = repo.commit_all("chore(review): 生成 2026-10-27 每日复盘 (v1)\n\nRun-Id: r")
    assert sha
    repo.push("review/daily/2026-10-27", "main")
    assert _git("branch", "-a", cwd=remote).find("review/daily/2026-10-27") >= 0
    repo.cleanup()


def test_git_repo_noop_commit_returns_none(tmp_path):
    remote = _make_bare_repo(tmp_path)
    repo = GitRepo(root=tmp_path / "checkout", allow_local=True)
    repo.clone(str(remote), "main")
    assert repo.commit_all("no changes") is None
    repo.cleanup()


def test_object_store_bundle_zip_has_manifest(tmp_path):
    bundle = write_bundle(
        data_root=tmp_path,
        workspace_id="ws-1",
        delivery_id="d-1",
        files={"每日复盘/2026/2026-10-27.md": "# 日报\n"},
        manifest={"files": 1},
    )
    with zipfile.ZipFile(bundle.zip_path) as archive:
        names = set(archive.namelist())
    assert "每日复盘/2026/2026-10-27.md" in names
    assert "manifest.json" in names
    assert bundle.file_hashes["每日复盘/2026/2026-10-27.md"]
