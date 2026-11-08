"""交付编排（ARCHITECTURE §8；API-CONTRACTS §3.7.10–§3.7.12）。

一次交付 = 复盘文件 + 三个专题页 + 索引页。Git 连接存在时走分支/commit/PR；
否则写本地对象存储包并生成 ZIP（AC-39 等效证据）。Git 不可用时自动降级为
对象存储，并在 `degraded_reasons` 记 `git_unavailable`。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from mylife_contract.constants import TOPIC_FILE_NAMES
from mylife_contract.errors import ErrorCode
from mylife_delivery.git_ops import GitDeliveryError, GitRepo
from mylife_delivery.index_page import render_index_inner, render_index_markdown
from mylife_delivery.manifest import (
    build_manifest,
    generated_at_iso,
    manifest_to_dict,
)
from mylife_delivery.markers import (
    ManualDetected,
    MarkerBroken,
    auto_region_hash,
    merge_auto_region,
)
from mylife_delivery.object_store import write_bundle
from mylife_delivery.pr_body import build_pr_body
from mylife_delivery.providers import ProviderError, provider_for
from mylife_delivery.renderer import review_path
from mylife_delivery.topics import render_topic_inner, render_topic_markdown
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repo_delivery as repo
from mylife_api import repo_review, repositories, services_topics
from mylife_api.crypto import CredentialDecryptError, decrypt_credential
from mylife_api.repositories import insert_audit
from mylife_api.settings import settings

logger = logging.getLogger("mylife.api.delivery")

STATUS_TO_API = {
    "pending": "pending",
    "committing": "pending",
    "committed": "pending",
    "pr_open": "pr_open",
    "merged": "merged",
    "closed": "none",
    "noop": "noop",
    "blocked": "blocked",
    "conflict": "blocked",
    "failed": "blocked",
}


def _delivery_id(workspace_id: UUID, review_id: UUID, version: int, target: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{workspace_id}|{review_id}|{version}|{target}")


def _decrypt_credential(connection: dict[str, Any], workspace_id: UUID) -> str | None:
    blob = connection.get("credential_ciphertext")
    if blob is None:
        return None
    for provider in (f"git:{connection['id']}", "git"):
        try:
            return decrypt_credential(
                bytes(blob), workspace_id=str(workspace_id), provider=provider
            )
        except CredentialDecryptError:
            continue
    raise RuntimeException("DELIVERY_CREDENTIAL_INVALID")


class RuntimeException(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _remote_url(connection: dict[str, Any]) -> str:
    if connection.get("remote_url"):
        return str(connection["remote_url"])
    repo_name = str(connection["repo_full_name"])
    if connection["provider"] == "gitlab":
        return f"https://gitlab.com/{repo_name}.git"
    return f"https://github.com/{repo_name}.git"


def _occurred_on(kind: str, period_key: str) -> date:
    if kind == "daily":
        return date.fromisoformat(period_key)
    if kind == "weekly":
        year, week = period_key.split("-W")
        return date.fromisocalendar(int(year), int(week), 1)
    return date(int(period_key), 1, 1)


async def _topic_items(
    session: AsyncSession, *, workspace_id: UUID
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for slug in ("errors", "methods", "actions"):
        rows = await repo_review.list_topic_items(
            session,
            workspace_id=workspace_id,
            topic=slug,
            status=None,
            include_pending=True,
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            occurrences = await repo_review.list_topic_occurrences(
                session, workspace_id=workspace_id, item_id=row["id"]
            )
            items.append(
                {
                    "canonical_key": row["canonical_key"],
                    "title": row["title"],
                    "status": row["status"],
                    "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"],
                    "occurrence_count": int(row["occurrence_count"]),
                    "conflicts": row.get("conflicts_json") or [],
                    "occurrences": [
                        {
                            "source_date": item["source_date"],
                            "source_path": item["source_path"],
                            "line_range": [item["line_start"], item["line_end"]]
                            if item.get("line_start")
                            and item.get("line_end") is not None
                            else None,
                            "quote": item["quote"],
                        }
                        for item in occurrences
                    ],
                }
            )
        grouped[slug] = items
    return grouped


def _previous_object_file(data_root: Path, workspace_id: str, path: str) -> str | None:
    base = Path(data_root) / "workspaces" / workspace_id / "artifacts" / "deliveries"
    if not base.exists():
        return None
    candidates = sorted(
        (candidate / "files" / path for candidate in base.iterdir() if candidate.is_dir()),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return None


def _merge_topic(
    *,
    existing: str | None,
    inner: str,
    full_markdown: str,
    previous_hash: str | None,
) -> tuple[str, str, str]:
    """返回 `(content, new_hash, state)`；state ∈ auto/manual。"""
    if existing is None:
        return full_markdown, auto_region_hash(inner), "auto"
    try:
        merged, digest = merge_auto_region(
            existing, inner, previous_auto_hash=previous_hash
        )
        return merged, digest, "auto"
    except (MarkerBroken, ManualDetected) as exc:
        logger.warning("topic marker refused: %s", exc.reason)
        return existing, previous_hash or "", "manual"


def _merge_index(existing: str | None, inner: str, full_markdown: str) -> tuple[str, bool]:
    if existing is None:
        return full_markdown, True
    try:
        merged, _ = merge_auto_region(existing, inner)
        return merged, True
    except (MarkerBroken, ManualDetected) as exc:
        logger.warning("index marker refused: %s", exc.reason)
        return existing, False


def _diff_summary(
    files: dict[str, str], read_existing
) -> dict[str, int]:
    summary = {"files_added": 0, "files_modified": 0, "files_unchanged": 0}
    for path, content in files.items():
        existing = read_existing(path)
        if existing is None:
            summary["files_added"] += 1
        elif existing != content:
            summary["files_modified"] += 1
        else:
            summary["files_unchanged"] += 1
    return summary


async def _prepare_files(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    reviews_root: str,
    read_existing,
    generated_at: str,
) -> tuple[dict[str, str], dict[str, str], str, dict[str, str], bool]:
    """计算待写入文件；返回 `(files, inner_hashes, index_content, states, conflict)`。"""
    root = reviews_root.rstrip("/")
    topic_items = await _topic_items(session, workspace_id=workspace_id)
    topic_states: dict[str, str] = {}
    files: dict[str, str] = {}
    inner_hashes: dict[str, str] = {}
    conflict = False

    for slug in ("errors", "methods", "actions"):
        relative = f"专题/{TOPIC_FILE_NAMES[slug]}.md"
        full_path = f"{root}/{relative}"
        inner = render_topic_inner(slug, topic_items[slug])
        full = render_topic_markdown(slug, topic_items[slug], generated_at=generated_at)
        state_row = await repo.get_topic_file(
            session, workspace_id=workspace_id, topic=slug
        )
        existing = read_existing(full_path)
        if (
            state_row is not None
            and state_row["status"] == "manual"
            and existing is not None
        ):
            # 人工编辑后自动重渲染暂停（FRONTEND-DESIGN §5.4）：保留文件与
            # 旧哈希基线，不再合并；文件被删除时按正常流程重建。
            files[full_path] = existing
            topic_states[slug] = "manual"
            continue
        content, digest, state = _merge_topic(
            existing=existing,
            inner=inner,
            full_markdown=full,
            previous_hash=(state_row or {}).get("auto_region_hash"),
        )
        if state == "manual":
            conflict = True
            await repo.upsert_topic_file(
                session,
                workspace_id=workspace_id,
                topic=slug,
                path=full_path,
                auto_region_hash=(state_row or {}).get("auto_region_hash"),
                status="manual",
                review_id=None,
                version=None,
            )
        else:
            inner_hashes[slug] = digest
            topic_states[slug] = state
        # 人工区/既有内容原样带入新交付；manual 时 content == existing（不覆盖）。
        files[full_path] = content

    latest = await repo.latest_reviews_by_kind(session, workspace_id=workspace_id)
    topic_counts = {
        slug: sum(1 for item in topic_items[slug] if item["status"] == "open")
        for slug in ("errors", "methods", "actions")
    }
    index_inner = render_index_inner(latest, topic_counts)
    index_full = render_index_markdown(
        latest, topic_counts, generated_at=generated_at
    )
    index_path = f"{root}/索引.md"
    existing_index = read_existing(index_path)
    index_content, index_ok = _merge_index(existing_index, index_inner, index_full)
    if not index_ok:
        conflict = True
    if index_content != (existing_index or ""):
        files[index_path] = index_content

    return files, inner_hashes, index_content, topic_states, conflict


async def deliver_review(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    run_id: UUID,
    review_id: UUID,
    version: int,
    kind: str,
    period_key: str,
    title: str,
    content_md: str,
    content_hash: str,
    degraded_reasons: list[str],
    verdict: dict[str, Any] | None,
) -> dict[str, Any]:
    """执行一次交付并写 `delivery_records`；返回结果字典。"""
    settings_row = await repositories.get_settings(session, workspace_id) or {}
    reviews_root = settings_row.get("reviews_root") or "wiki/复盘"
    root = reviews_root.rstrip("/")
    generated_at = datetime.now(UTC)
    generated_iso = generated_at_iso(generated_at)

    sections = [
        {
            "section_key": row["section_key"],
            "ordinal": row["ordinal"],
            "content_md": row["content_md"],
        }
        for row in await repo_review.list_sections(
            session, workspace_id=workspace_id, review_id=review_id, version=version
        )
    ]
    citations = [
        {
            "claim_id": row["claim_id"],
            "evidence_id": row["evidence_id"],
            "source_path": row.get("source_path"),
            "line_range": [row["line_start"], row["line_end"]]
            if row.get("line_start") is not None
            else None,
            "quote": row.get("quote") or "",
        }
        for row in await repo_review.list_citations(
            session, workspace_id=workspace_id, review_id=review_id, version=version
        )
    ]

    connection = await repo.get_connection(session, workspace_id=workspace_id, role="output")
    review_full_path = review_path(root, kind, period_key)
    relative_review = review_full_path[len(root) + 1 :]

    def read_git(repo_root: Path):
        def reader(path: str) -> str | None:
            target = repo_root / path
            return target.read_text(encoding="utf-8") if target.exists() else None

        return reader

    def read_object(_: str):
        def reader(path: str) -> str | None:
            return _previous_object_file(settings.data_root, str(workspace_id), path)

        return reader

    degraded = bool(degraded_reasons)
    reasons = list(degraded_reasons)
    target = "git" if connection is not None else "object_store"
    provider_name = connection["provider"] if connection else None
    branch = None
    base_branch = connection["base_branch"] if connection else "main"
    commit_sha = None
    pr_number = None
    pr_url = None
    merged_commit = None
    merged_at = None
    error_code = None
    auto_merge = False
    status = "pending"
    manifest = None
    diff_summary = {"files_added": 0, "files_modified": 0, "files_unchanged": 0}
    working_dir: Path | None = None
    git_repo: GitRepo | None = None
    inner_hashes_all: dict[str, str] = {}

    previous = await repo.latest_delivery_for_review(
        session, workspace_id=workspace_id, review_id=review_id
    )
    # 只有“同一版本重试且内容未变、且上次交付成功”才是 noop；新版本即使正文
    # 相同也要重新渲染专题页与索引（确认/解决专题条目会改变这些文件）。
    # 失败/阻断的记录必须真正重试，不能被 noop 短路（重试端点依赖这一点）。
    previous_ok = previous is not None and previous.get("status") in {
        "committed",
        "pr_open",
        "merged",
        "noop",
    }
    if (
        previous_ok
        and int(previous.get("version") or 0) == version
        and previous.get("content_hash") == content_hash
    ):
        status = "noop"

    if status != "noop":
        try:
            if connection is not None:
                credential = _decrypt_credential(connection, workspace_id)
                pat = credential if connection["auth_type"] in {"pat", "github_app"} else None
                key = credential if connection["auth_type"] == "deploy_key" else None
                working_dir = Path(tempfile.mkdtemp(prefix="mylife-delivery-"))
                git_repo = GitRepo(
                    root=working_dir, allow_local=settings.allow_local_git, pat=pat,
                    private_key=key,
                )
                await asyncio.to_thread(
                    git_repo.clone, _remote_url(connection), base_branch
                )
                branch = f"review/{kind}/{period_key}"
                await asyncio.to_thread(git_repo.ensure_branch, branch, base_branch)
                files, inner_hashes, index_content, _states, conflict = await _prepare_files(
                    session,
                    workspace_id=workspace_id,
                    reviews_root=root,
                    read_existing=read_git(working_dir),
                    generated_at=generated_iso,
                )
                files[review_full_path] = content_md
                diff_summary = _diff_summary(files, read_git(working_dir))
                inner_hashes_all = inner_hashes
                topics_map = {
                    slug: files.get(f"{root}/专题/{TOPIC_FILE_NAMES[slug]}.md", "")
                    for slug in ("errors", "methods", "actions")
                }
                pr_body = build_pr_body(
                    kind=kind,
                    period_key=period_key,
                    version=version,
                    review_id=str(review_id),
                    run_id=str(run_id),
                    sections=sections,
                    citations=citations,
                    targets=[
                        {"path": f"{root}/{relative_review}", "mode": "update_allowed"},
                        *[
                            {
                                "path": f"{root}/专题/{TOPIC_FILE_NAMES[slug]}.md",
                                "mode": "update_allowed",
                            }
                            for slug in ("errors", "methods", "actions")
                        ],
                        {"path": f"{root}/索引.md", "mode": "update_allowed"},
                    ],
                    verdict=verdict,
                    degraded_reasons=reasons,
                )
                auto_merge = bool(
                    kind == "daily"
                    and settings_row.get("auto_merge_daily")
                    and (verdict or {}).get("decision") == "APPROVE"
                )
                manifest = build_manifest(
                    kind=kind,
                    period_key=period_key,
                    version=version,
                    review_id=str(review_id),
                    run_id=str(run_id),
                    workspace_id=str(workspace_id),
                    reviews_root=root,
                    review_path=relative_review,
                    review_content=content_md,
                    topic_files=topics_map,
                    index_content=index_content,
                    pr_body=pr_body,
                    base_branch=base_branch,
                    auto_merge=auto_merge,
                    degraded=degraded,
                    degraded_reasons=reasons,
                )
                await asyncio.to_thread(git_repo.write_files, files)
                commit_sha = await asyncio.to_thread(
                    git_repo.commit_all, manifest.commit_message
                )
                if commit_sha is None:
                    status = "noop"
                else:
                    await asyncio.to_thread(git_repo.push, branch, base_branch)
                    commit_sha = await asyncio.to_thread(git_repo.head_sha)
                    provider = provider_for(
                        provider=connection["provider"],
                        repo_full_name=connection["repo_full_name"],
                        token=credential or "",
                        api_base_url=connection.get("api_base_url"),
                    )
                    pr = await asyncio.to_thread(
                        provider.create_or_update_pr,
                        title=manifest.pr.title,
                        body=manifest.pr.body_md,
                        head_branch=branch,
                        base_branch=base_branch,
                    )
                    pr_number, pr_url = pr.number, pr.url
                    status = "pr_open"
                    if auto_merge:
                        merged = await asyncio.to_thread(provider.merge_pr, pr.number)
                        status = "merged"
                        merged_commit = merged.get("merge_commit")
                        merged_at = datetime.now(UTC)
                    if conflict:
                        status = "conflict"
                        error_code = ErrorCode.GIT_MARKER_BROKEN.value
            else:
                files, inner_hashes, index_content, _states, conflict = await _prepare_files(
                    session,
                    workspace_id=workspace_id,
                    reviews_root=root,
                    read_existing=read_object(working_dir),
                    generated_at=generated_iso,
                )
                files[review_full_path] = content_md
                diff_summary = _diff_summary(files, read_object(working_dir))
                inner_hashes_all = inner_hashes
                topics_map = {
                    slug: files.get(f"{root}/专题/{TOPIC_FILE_NAMES[slug]}.md", "")
                    for slug in ("errors", "methods", "actions")
                }
                pr_body = build_pr_body(
                    kind=kind,
                    period_key=period_key,
                    version=version,
                    review_id=str(review_id),
                    run_id=str(run_id),
                    sections=sections,
                    citations=citations,
                    targets=[
                        {"path": f"{root}/{relative_review}", "mode": "update_allowed"},
                        {
                            "path": f"{root}/专题/{TOPIC_FILE_NAMES['errors']}.md",
                            "mode": "update_allowed",
                        },
                        {
                            "path": f"{root}/专题/{TOPIC_FILE_NAMES['methods']}.md",
                            "mode": "update_allowed",
                        },
                        {
                            "path": f"{root}/专题/{TOPIC_FILE_NAMES['actions']}.md",
                            "mode": "update_allowed",
                        },
                        {"path": f"{root}/索引.md", "mode": "update_allowed"},
                    ],
                    verdict=verdict,
                    degraded_reasons=reasons,
                )
                manifest = build_manifest(
                    kind=kind,
                    period_key=period_key,
                    version=version,
                    review_id=str(review_id),
                    run_id=str(run_id),
                    workspace_id=str(workspace_id),
                    reviews_root=root,
                    review_path=relative_review,
                    review_content=content_md,
                    topic_files=topics_map,
                    index_content=index_content,
                    pr_body=pr_body,
                    base_branch=base_branch,
                    auto_merge=False,
                    degraded=degraded,
                    degraded_reasons=reasons,
                )
                delivery_id = _delivery_id(workspace_id, review_id, version, "object_store")
                bundle = await asyncio.to_thread(
                    write_bundle,
                    data_root=settings.data_root,
                    workspace_id=str(workspace_id),
                    delivery_id=str(delivery_id),
                    files=files,
                    manifest=manifest_to_dict(manifest),
                )
                diff_summary = {
                    "files_added": len(files),
                    "files_modified": 0,
                    "files_unchanged": 0,
                }
                status = "committed"
                if conflict:
                    status = "conflict"
                    error_code = ErrorCode.GIT_MARKER_BROKEN.value
                _ = bundle
        except GitDeliveryError as exc:
            logger.warning("git delivery failed: %s", exc.code)
            if connection is not None:
                # Git 不可用 → 对象存储降级（AC-39）。
                if "git_unavailable" not in reasons:
                    reasons.append("git_unavailable")
                degraded = True
                target = "object_store"
                status = "committed"
                error_code = exc.code
                try:
                    (
                        files_fallback,
                        inner_hashes_all,
                        index_content,
                        _st,
                        conflict,
                    ) = await _prepare_files(
                        session,
                        workspace_id=workspace_id,
                        reviews_root=root,
                        read_existing=read_object(working_dir),
                        generated_at=generated_iso,
                    )
                    files_fallback[review_full_path] = content_md
                    topics_map = {
                        slug: files_fallback.get(
                            f"{root}/专题/{TOPIC_FILE_NAMES[slug]}.md", ""
                        )
                        for slug in ("errors", "methods", "actions")
                    }
                    manifest = build_manifest(
                        kind=kind,
                        period_key=period_key,
                        version=version,
                        review_id=str(review_id),
                        run_id=str(run_id),
                        workspace_id=str(workspace_id),
                        reviews_root=root,
                        review_path=relative_review,
                        review_content=content_md,
                        topic_files=topics_map,
                        index_content=index_content,
                        pr_body="",
                        base_branch=base_branch,
                        auto_merge=False,
                        degraded=True,
                        degraded_reasons=reasons,
                    )
                    delivery_id = _delivery_id(
                        workspace_id, review_id, version, "object_store"
                    )
                    await asyncio.to_thread(
                        write_bundle,
                        data_root=settings.data_root,
                        workspace_id=str(workspace_id),
                        delivery_id=str(delivery_id),
                        files=files_fallback,
                        manifest=manifest_to_dict(manifest),
                    )
                    if conflict:
                        status = "conflict"
                        error_code = ErrorCode.GIT_MARKER_BROKEN.value
                except Exception:  # noqa: BLE001 - 降级路径失败按 failed 落库
                    status = "failed"
            else:
                status = "failed"
                error_code = exc.code
        except ProviderError as exc:
            logger.warning("provider failed: %s %s", exc.code, exc.reason)
            status = "failed"
            error_code = exc.code
        except RuntimeException as exc:
            status = "failed"
            error_code = exc.code
        except Exception:  # noqa: BLE001 - 交付失败不得静默
            logger.exception("delivery crashed")
            status = "failed"
            error_code = ErrorCode.INTERNAL_ERROR.value
        finally:
            if git_repo is not None:
                git_repo.cleanup()
            if working_dir is not None:
                shutil.rmtree(working_dir, ignore_errors=True)

    if manifest is None:
        manifest = build_manifest(
            kind=kind,
            period_key=period_key,
            version=version,
            review_id=str(review_id),
            run_id=str(run_id),
            workspace_id=str(workspace_id),
            reviews_root=root,
            review_path=relative_review,
            review_content=content_md,
            topic_files={},
            index_content="",
            pr_body="",
            base_branch=base_branch,
            auto_merge=False,
            degraded=degraded,
            degraded_reasons=reasons,
        )

    if status == "noop":
        target = "object_store" if connection is None else "git"
        branch = None if connection is None else f"review/{kind}/{period_key}"
        commit_sha = previous.get("commit_sha") if previous else None

    delivery_id = _delivery_id(workspace_id, review_id, version, target)
    await repo.upsert_delivery(
        session,
        delivery_id=delivery_id,
        workspace_id=workspace_id,
        review_id=review_id,
        version=version,
        target=target,
        provider=provider_name if target == "git" else None,
        branch=branch,
        base_branch=base_branch,
        commit_sha=commit_sha,
        pr_number=pr_number,
        pr_url=pr_url,
        status=status,
        merged_commit=merged_commit,
        merged_at=merged_at,
        auto_merge=auto_merge,
        error_code=error_code,
        degraded=degraded,
        content_hash=content_hash,
        manifest=manifest_to_dict(manifest),
        diff_summary=diff_summary,
    )
    if commit_sha and target == "git":
        await repo.set_version_git_sha(
            session,
            workspace_id=workspace_id,
            review_id=review_id,
            version=version,
            sha=commit_sha,
        )
    api_status = STATUS_TO_API.get(status, "blocked")
    if target == "object_store" and status in {"committed", "pending"}:
        # 对象存储是 Git 不可用时的降级交付（API 枚举用 ui_diff 表达）。
        api_status = "ui_diff"
    await repo.set_review_delivery_status(
        session, workspace_id=workspace_id, review_id=review_id, status=api_status
    )
    await repo.upsert_review_search(
        session,
        workspace_id=workspace_id,
        review_id=review_id,
        version=version,
        kind=kind,
        period_key=period_key,
        title=title,
        path=review_full_path,
        content=content_md,
        content_hash=content_hash,
        occurred_on=_occurred_on(kind, period_key),
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=None,
        action="delivery.auto_merged" if status == "merged" and auto_merge else "delivery.created",
        target=str(delivery_id),
        detail={
            "review_id": str(review_id),
            "version": version,
            "target": target,
            "status": status,
            "pr_number": pr_number,
            "commit_sha": commit_sha,
        },
    )
    if status in {"conflict", "failed"} or error_code:
        await repo.insert_event(
            session,
            workspace_id=workspace_id,
            provider=provider_name or "object_store",
            event="delivery.failed" if status == "failed" else "delivery.conflict",
            payload={"review_id": str(review_id), "error_code": error_code},
            status="failed" if status == "failed" else "handled",
        )

    await services_topics.commit_render_state(
        session,
        workspace_id=workspace_id,
        review_id=review_id,
        version=version,
        hashes=inner_hashes_all,
    )
    # 仪表盘有 60s 进程内缓存；交付/版本变化后立即失效，避免陈旧计数。
    from mylife_api.services_dashboard import clear_dashboard_cache

    clear_dashboard_cache()

    return {
        "delivery_id": delivery_id,
        "target": target,
        "status": status,
        "branch": branch,
        "commit_sha": commit_sha,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "auto_merge": auto_merge,
        "degraded": degraded,
        "error_code": error_code,
    }


# ---------------------------------------------------------------------------
# 查询 / 重试（API-CONTRACTS §3.7.10–§3.7.12）
# ---------------------------------------------------------------------------

API_STATUS = {
    "pending": "pending",
    "committing": "pending",
    "committed": "committed",
    "pr_open": "pr_open",
    "merged": "merged",
    "closed": "closed",
    "noop": "noop",
    "blocked": "blocked",
    "conflict": "blocked",
    "failed": "failed",
}


def _record(row: dict[str, Any]):
    from mylife_contract.delivery import DeliveryRecord

    target = row["target"]
    if target in {"db_only", "ui_diff"}:
        target = "object_store"
    provider = row.get("provider")
    if provider not in {"github", "gitlab"}:
        provider = None
    return DeliveryRecord(
        id=row["id"],
        review_id=row["review_id"],
        version=int(row["version"]),
        target=target,
        provider=provider,
        branch=row.get("branch"),
        base_branch=row.get("base_branch"),
        commit_sha=row.get("commit_sha"),
        pr_number=row.get("pr_number"),
        pr_url=row.get("pr_url"),
        status=API_STATUS.get(row["status"], "blocked"),
        merged_commit=row.get("merged_commit"),
        auto_merge=bool(row.get("auto_merge")),
        error_code=row.get("error_code"),
        degraded=bool(row.get("degraded")),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def encode_delivery_cursor(created_at: datetime, delivery_id: UUID) -> str:
    import base64
    import json

    payload = json.dumps(
        {"t": created_at.isoformat(), "id": str(delivery_id)}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_delivery_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    import base64
    import json

    from mylife_contract.errors import ErrorCode

    from mylife_api.errors import AppError

    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        payload = json.loads(raw)
        return datetime.fromisoformat(payload["t"]), UUID(payload["id"])
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            ErrorCode.VALIDATION_ERROR, details={"reason": "CURSOR_INVALID"}
        ) from exc


async def list_delivery_records(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    review_id: UUID | None,
    status: str | None,
    limit: int,
    cursor: str | None = None,
):
    from mylife_contract.delivery import DeliveryList

    rows = await repo.list_deliveries(
        session,
        workspace_id=workspace_id,
        review_id=review_id,
        status=status,
        limit=limit + 1,
        after=decode_delivery_cursor(cursor),
    )
    has_more = len(rows) > limit
    items = [_record(row) for row in rows[:limit]]
    next_cursor = (
        encode_delivery_cursor(rows[limit - 1]["created_at"], rows[limit - 1]["id"])
        if has_more and rows
        else None
    )
    return DeliveryList(items=items, next_cursor=next_cursor, has_more=has_more)


async def get_delivery_detail(
    session: AsyncSession, *, workspace_id: UUID, delivery_id: UUID
):
    from mylife_contract.delivery import (
        DeliveryDetail,
        DeliveryDiffSummary,
        DeliveryManifest,
    )

    row = await repo.get_delivery(session, workspace_id=workspace_id, delivery_id=delivery_id)
    if row is None:
        from mylife_contract.errors import ErrorCode

        from mylife_api.errors import AppError

        raise AppError(ErrorCode.DELIVERY_NOT_FOUND)
    manifest_data = row.get("manifest_json") or {}
    if not manifest_data:
        # 历史/异常行没有 manifest：返回最小占位而不是 500。
        review = await repo_review.get_review(
            session, workspace_id=workspace_id, review_id=row["review_id"]
        )
        manifest = DeliveryManifest(
            branch=row.get("branch") or "",
            targets=[
                {
                    "kind": "object_store",
                    "path": (review or {}).get("git_path") or "review.md",
                    "content_hash": row.get("content_hash") or "",
                    "mode": "update_allowed",
                }
            ],
            commit_message="",
            pr=None,
            review_row={
                "review_id": row["review_id"],
                "version": int(row["version"]),
                "content_hash": row.get("content_hash") or "",
                "git_path": (review or {}).get("git_path") or "",
            },
        )
    else:
        manifest = DeliveryManifest.model_validate(manifest_data)
    diff = DeliveryDiffSummary.model_validate(row.get("diff_summary_json") or {})
    return DeliveryDetail(
        delivery=_record(row), manifest=manifest, diff_summary=diff
    )


async def retry_delivery(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
    delivery_id: UUID,
) -> dict[str, Any]:
    from mylife_contract.errors import ErrorCode

    from mylife_api.errors import AppError

    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    row = await repo.get_delivery(session, workspace_id=workspace_id, delivery_id=delivery_id)
    if row is None:
        raise AppError(ErrorCode.DELIVERY_NOT_FOUND)
    review = await repo_review.get_review(
        session, workspace_id=workspace_id, review_id=row["review_id"]
    )
    if review is None:
        raise AppError(ErrorCode.NOT_FOUND)
    version_row = await repo_review.get_version(
        session,
        workspace_id=workspace_id,
        review_id=row["review_id"],
        version=int(row["version"]),
    )
    if version_row is None:
        raise AppError(ErrorCode.REVIEW_VERSION_NOT_FOUND)
    run_rows = await repo_review.list_runs_for_review(
        session, workspace_id=workspace_id, review_id=row["review_id"]
    )
    run_id = run_rows[0]["id"] if run_rows else uuid5(NAMESPACE_URL, f"retry:{delivery_id}")
    verdict = review.get("verdict_json")
    if isinstance(verdict, str):
        import json

        verdict = json.loads(verdict)
    await deliver_review(
        session,
        workspace_id=workspace_id,
        run_id=run_id,
        review_id=row["review_id"],
        version=int(row["version"]),
        kind=review["kind"],
        period_key=review["period_key"],
        title=review["title"],
        content_md=version_row["content_md"],
        content_hash=version_row["content_hash"],
        degraded_reasons=list(review.get("degraded_reasons") or []),
        verdict=verdict,
    )
    await session.commit()
    return {"delivery_id": delivery_id, "status": "pending"}


def object_zip_path(*, workspace_id: UUID, delivery_id: UUID) -> Path:
    return (
        Path(settings.data_root)
        / "workspaces"
        / str(workspace_id)
        / "artifacts"
        / "deliveries"
        / str(delivery_id)
        / "artifact.zip"
    )
