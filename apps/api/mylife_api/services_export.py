"""导出 ZIP（API-CONTRACTS §3.11.1–§3.11.3；AC-55 导出半部 / AC-64）。

产物布局（DIRECTORY-CONSTRAINTS §3，PHASE5-SPEC D2）：

    {data_root}/workspaces/{ws}/exports/{export_id}.zip
    documents/...          原文 Markdown（按 path 还原目录）
    reviews/...            按 reviews_root 结构
    conversations/*.md     每个对话一个文件
    settings.json          脱敏配置（无凭据）
    usage.csv              全量 usage_events
    audit.jsonl            include 含 audit 时
    manifest.json          workspace_id / 导出时间 / 逐文件 sha256 / 计数

下载直传（D1），不经 MinIO。过期由 dispatcher 置 `expired` 并删除文件。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from mylife_contract.constants import (
    EXPORT_AUDIT_NAME,
    EXPORT_MANIFEST_NAME,
    EXPORT_SETTINGS_NAME,
    EXPORT_USAGE_NAME,
)
from mylife_contract.errors import ErrorCode
from mylife_contract.exports import ExportCreate, ExportCreated, ExportJob
from mylife_runtime.paths import PathRejected, safe_join
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api.errors import AppError
from mylife_api.ids import uuid7
from mylife_api.repositories import insert_audit
from mylife_api.settings import settings as app_settings

logger = logging.getLogger("mylife.api.export")

#: 每个 workspace 每日导出次数（API §8.1）。
DAILY_LIMIT = 3

_DB_TO_WIRE = {"succeeded": "ready", "queued": "queued", "running": "running"}


def exports_root(data_root: Path, workspace_id: UUID | str) -> Path:
    return Path(data_root) / "workspaces" / str(workspace_id) / "exports"


def artifact_path(data_root: Path, workspace_id: UUID | str, export_id: UUID | str) -> Path:
    return exports_root(data_root, workspace_id) / f"{export_id}.zip"


def _job_model(row: dict[str, Any]) -> ExportJob:
    from mylife_contract.exports import ExportError, ExportProgress

    status = row["status"]
    error = row.get("error")
    if isinstance(error, str):
        error_obj = ExportError(code=error, message=error) if error else None
    elif isinstance(error, dict):
        error_obj = ExportError(
            code=error.get("code", "EXPORT_FAILED"),
            message=error.get("message", ""),
        )
    else:
        error_obj = None
    progress = row.get("progress_json") or {}
    return ExportJob(
        id=row["id"],
        scope=row["scope"],
        status=_DB_TO_WIRE.get(status, status),  # type: ignore[arg-type]
        progress=ExportProgress(
            files=int(progress.get("files", 0)), bytes=int(progress.get("bytes", 0))
        ),
        artifact_bytes=row.get("artifact_bytes"),
        created_at=row["created_at"],
        expires_at=row.get("expires_at"),
        error=error_obj,
    )


async def create_export(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
    payload: ExportCreate,
    idempotency_key: str | None,
) -> ExportCreated:
    if role not in {"owner", "admin"}:
        raise AppError(ErrorCode.FORBIDDEN)
    if not idempotency_key:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details={"field": "Idempotency-Key", "reason": "REQUIRED"},
        )

    # 同 scope 活跃任务唯一（部分唯一索引兜底，这里给出可读错误）。
    active = (
        await session.execute(
            text(
                "SELECT id FROM export_jobs WHERE workspace_id = :ws AND scope = :scope "
                "AND status IN ('queued','running') LIMIT 1"
            ),
            {"ws": workspace_id, "scope": payload.scope},
        )
    ).first()
    if active is not None:
        raise AppError(ErrorCode.EXPORT_LIMIT, details={"reason": "ACTIVE_EXPORT"})

    since = datetime.now(UTC) - timedelta(days=1)
    daily = (
        await session.execute(
            text(
                "SELECT count(*) FROM export_jobs WHERE workspace_id = :ws "
                "AND created_at >= :since"
            ),
            {"ws": workspace_id, "since": since},
        )
    ).scalar_one()
    if int(daily) >= DAILY_LIMIT:
        raise AppError(ErrorCode.EXPORT_LIMIT, details={"reason": "DAILY_LIMIT"})

    export_id = uuid7()
    expires_at = datetime.now(UTC) + timedelta(days=app_settings.export_ttl_days)
    await session.execute(
        text(
            "INSERT INTO export_jobs (id, workspace_id, requested_by, status, scope, "
            "  include_json, format, expires_at) "
            "VALUES (:id, :ws, :user, 'queued', :scope, CAST(:include AS jsonb), "
            "        :format, :expires)"
        ),
        {
            "id": export_id,
            "ws": workspace_id,
            "user": user_id,
            "scope": payload.scope,
            "include": json.dumps(payload.include),
            "format": payload.format,
            "expires": expires_at,
        },
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="export.requested",
        target=str(export_id),
        detail={"scope": payload.scope, "include": payload.include},
    )
    return ExportCreated(export_id=export_id, expires_at=expires_at)


async def get_export(
    session: AsyncSession, *, workspace_id: UUID, export_id: UUID
) -> ExportJob:
    row = (
        await session.execute(
            text(
                "SELECT id, scope, status, progress_json, artifact_bytes, created_at, "
                "       expires_at, error FROM export_jobs "
                "WHERE workspace_id = :ws AND id = :id"
            ),
            {"ws": workspace_id, "id": export_id},
        )
    ).first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return _job_model(dict(row._mapping))


def _safe_arcname(path: str) -> str:
    """导出包内路径：拒绝穿越与绝对路径，去掉首部 `/`。"""
    try:
        safe_join(Path("."), path, vault=True)
    except PathRejected as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            details={"field": "path", "reason": "PATH_REJECTED", "path": path},
        ) from exc
    return path.lstrip("/").replace("\\", "/")


async def build_export(
    session: AsyncSession, *, workspace_id: UUID, export_id: UUID
) -> dict[str, Any]:
    """构建 ZIP 并回写状态；返回对账摘要（供 AC-55 证据）。"""
    job = (
        await session.execute(
            text(
                "SELECT id, include_json, status FROM export_jobs "
                "WHERE workspace_id = :ws AND id = :id"
            ),
            {"ws": workspace_id, "id": export_id},
        )
    ).first()
    if job is None:
        raise AppError(ErrorCode.NOT_FOUND)
    include = set(job.include_json or [])
    # RLS 上下文在 commit 后会清空；构建期间不提交，避免中途丢租户。
    await session.execute(
        text("UPDATE export_jobs SET status = 'running', updated_at = now() WHERE id = :id"),
        {"id": export_id},
    )

    files: dict[str, bytes] = {}
    counts = {"documents": 0, "reviews": 0, "conversations": 0, "usage_events": 0, "audit_logs": 0}

    if "documents" in include:
        rows = (
            await session.execute(
                text(
                    "SELECT path, content FROM documents WHERE workspace_id = :ws "
                    "ORDER BY path"
                ),
                {"ws": workspace_id},
            )
        ).all()
        for row in rows:
            arc = _safe_arcname(f"documents/{row.path}")
            files[arc] = (row.content or "").encode("utf-8")
        counts["documents"] = len(rows)

    if "reviews" in include:
        rows = (
            await session.execute(
                text(
                    "SELECT kind, period_key, version, title, content FROM reviews r "
                    "WHERE workspace_id = :ws AND version = "
                    "  (SELECT max(version) FROM reviews x WHERE x.id = r.id) "
                    "ORDER BY kind, period_key"
                ),
                {"ws": workspace_id},
            )
        ).all()
        review_count = 0
        for row in rows:
            arc = _safe_arcname(f"reviews/{row.kind}/{row.period_key}.md")
            files[arc] = (row.content or "").encode("utf-8")
            review_count += 1
        counts["reviews"] = review_count

    if "conversations" in include:
        rows = (
            await session.execute(
                text(
                    "SELECT c.id, c.title, m.role, m.content, m.created_at "
                    "FROM conversations c LEFT JOIN messages m "
                    "  ON m.conversation_id = c.id AND m.workspace_id = c.workspace_id "
                    "WHERE c.workspace_id = :ws AND c.deleted_at IS NULL "
                    "ORDER BY c.created_at, m.created_at"
                ),
                {"ws": workspace_id},
            )
        ).all()
        grouped: dict[UUID, dict[str, Any]] = {}
        for row in rows:
            entry = grouped.setdefault(
                row.id, {"title": row.title, "lines": []}
            )
            if row.content is not None:
                entry["lines"].append(f"## {row.role}\n\n{row.content}\n")
        for conv_id, entry in grouped.items():
            arc = _safe_arcname(f"conversations/{conv_id}.md")
            body = f"# {entry['title']}\n\n" + "\n".join(entry["lines"])
            files[arc] = body.encode("utf-8")
        counts["conversations"] = len(grouped)

    if "settings" in include:
        # 脱敏：只导出非敏感配置，绝不含凭据密文（AC-64）。
        row = (
            await session.execute(
                text(
                    "SELECT timezone, daily_at, monthly_at, yearly_at, reviews_root, "
                    "       retrieval_top_k, context_window_tokens, auto_merge_daily "
                    "FROM workspace_settings WHERE workspace_id = :ws"
                ),
                {"ws": workspace_id},
            )
        ).first()
        settings_payload = dict(row._mapping) if row else {}
        for key, value in list(settings_payload.items()):
            if isinstance(value, (bytes, memoryview)):
                settings_payload.pop(key)
        settings_payload["provider_credentials"] = "redacted"
        files[EXPORT_SETTINGS_NAME] = json.dumps(
            settings_payload, ensure_ascii=False, indent=2, default=str
        ).encode("utf-8")

    if "usage" in include:
        rows = (
            await session.execute(
                text(
                    "SELECT kind, model, prompt_tokens, completion_tokens, latency_ms, "
                    "       cost, degraded, created_at FROM usage_events "
                    "WHERE workspace_id = :ws ORDER BY created_at"
                ),
                {"ws": workspace_id},
            )
        ).all()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "kind",
                "model",
                "prompt_tokens",
                "completion_tokens",
                "latency_ms",
                "cost",
                "degraded",
                "created_at",
            ]
        )
        for row in rows:
            writer.writerow(
                [row.kind, row.model, row.prompt_tokens, row.completion_tokens,
                 row.latency_ms, row.cost, row.degraded, row.created_at.isoformat()]
            )
        files[EXPORT_USAGE_NAME] = buffer.getvalue().encode("utf-8")
        counts["usage_events"] = len(rows)

    if "audit" in include:
        rows = (
            await session.execute(
                text(
                    "SELECT action, target, detail, created_at FROM audit_logs "
                    "WHERE workspace_id = :ws ORDER BY created_at"
                ),
                {"ws": workspace_id},
            )
        ).all()
        lines = [
            json.dumps(
                {
                    "action": row.action,
                    "target": row.target,
                    # 脱敏：detail 已按 insert_audit 截断；再剔除敏感键。
                    "detail": {
                        k: v
                        for k, v in (row.detail or {}).items()
                        if k.lower() not in {"api_key", "key", "password", "token", "secret"}
                    },
                    "created_at": row.created_at.isoformat(),
                },
                ensure_ascii=False,
            )
            for row in rows
        ]
        files[EXPORT_AUDIT_NAME] = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        counts["audit_logs"] = len(rows)

    manifest_files = [
        {
            "path": arc,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        for arc, content in sorted(files.items())
    ]
    manifest = {
        "workspace_id": str(workspace_id),
        "exported_at": datetime.now(UTC).isoformat(),
        "include": sorted(include),
        "counts": counts,
        "files": manifest_files,
    }

    target = artifact_path(app_settings.data_root, workspace_id, export_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for arc, content in sorted(files.items()):
            archive.writestr(arc, content)
        archive.writestr(
            EXPORT_MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    tmp.replace(target)

    total_bytes = sum(len(c) for c in files.values())
    await session.execute(
        text(
            "UPDATE export_jobs SET status = 'ready', artifact_key = :key, "
            "  artifact_bytes = :size, progress_json = CAST(:progress AS jsonb), "
            "  updated_at = now() WHERE id = :id"
        ),
        {
            "key": str(target),
            "size": total_bytes,
            "progress": json.dumps({"files": len(files), "bytes": total_bytes}),
            "id": export_id,
        },
    )
    await insert_audit(
        session,
        workspace_id=workspace_id,
        user_id=None,
        action="export.completed",
        target=str(export_id),
        detail={"files": len(files), "counts": counts},
    )
    return {
        "export_id": str(export_id),
        "files": len(files),
        "bytes": total_bytes,
        "counts": counts,
        "manifest_files": len(manifest_files),
    }


async def fail_export(
    session: AsyncSession, *, workspace_id: UUID, export_id: UUID, reason: str
) -> None:
    await session.execute(
        text(
            "UPDATE export_jobs SET status = 'failed', "
            "  error = CAST(:error AS jsonb), updated_at = now() WHERE id = :id"
        ),
        {"error": json.dumps({"code": reason, "message": reason}), "id": export_id},
    )


async def purge_expired_exports(session: AsyncSession) -> int:
    """dispatcher：过期导出置 `expired` 并删除文件（7 天后）。

    `export_jobs` 是 FORCE RLS，dispatcher 无租户上下文时 UPDATE 恒 0 行；
    逐行按 workspace 绑定上下文再更新。
    """
    from mylife_api.db import set_rls_context

    rows = await session.execute(text("SELECT * FROM dispatch_due_exports(50)"))
    purged = 0
    for row in rows:
        await set_rls_context(session, row.workspace_id, None)
        key = row.artifact_key
        if key:
            try:
                Path(key).unlink(missing_ok=True)  # noqa: ASYNC240 - 单文件删除
            except OSError:
                logger.warning("export file delete failed id=%s", row.id)
        await session.execute(
            text(
                "UPDATE export_jobs SET status = 'expired', updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": row.id},
        )
        purged += 1
    await session.commit()
    return purged


__all__ = [
    "artifact_path",
    "build_export",
    "create_export",
    "exports_root",
    "fail_export",
    "get_export",
    "purge_expired_exports",
]
