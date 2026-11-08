"""增量索引器：documents / chunks / links / changes / snapshot（FR-B5；AC-11/12/14）。

幂等与增量：
- `raw_hash` 相同 → 不解析、不 embedding（AC-12 “parser 调用增量 0”）；
- `content_hash` 相同但 raw 不同 → 只更新 raw_hash/mtime，不动 indexed_at；
- 重命名：新路径的 content_hash 命中已删除文档 → 迁移 document_id，chunk 与 embedding 保留。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from mylife_contract.chunk_tables import BASE_CHUNK_TABLE, validate_chunk_table
from mylife_retrieval.embedding import EmbeddingProvider, EmbeddingUnavailable
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_worker.db import bind_workspace
from mylife_worker.ids import uuid7
from mylife_worker.markdown import (
    PARSER_VERSION,
    ParsedDocument,
    ParseError,
    json_default,
    parse_document,
)
from mylife_worker.task_ledger import (
    delete_document_occurrences,
    rebuild_tasks,
    sync_document_occurrences,
)


@dataclass(slots=True)
class SourceFile:
    path: str
    data: bytes
    mtime: datetime | None = None


@dataclass(slots=True)
class IngestOutcome:
    counters: dict[str, int] = field(
        default_factory=lambda: {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "failed": 0,
            "task_occurrences": 0,
            "tasks_skipped_no_date": 0,
            "date_conflicts": 0,
        }
    )
    failures: list[dict[str, Any]] = field(default_factory=list)
    snapshot_id: UUID | None = None
    manifest_digest: str = ""
    degraded: bool = False


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", data, 0, 1, "unsupported encoding")


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _load_documents(session: AsyncSession, workspace_id: UUID) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, path, content_hash, raw_hash, indexed_at "
            "FROM documents WHERE workspace_id = :ws"
        ),
        {"ws": workspace_id},
    )
    return {row.path: dict(row._mapping) for row in result}


async def _active_chunk_target(
    session: AsyncSession, workspace_id: UUID
) -> tuple[str, UUID | None]:
    """解析当前 workspace 的向量表与 profile；无 active profile 时为基表。"""
    row = (
        await session.execute(
            text(
                "SELECT p.id, p.chunk_table FROM embedding_profiles p "
                "JOIN workspace_settings s ON s.embedding_profile_id = p.id "
                "WHERE s.workspace_id = :ws"
            ),
            {"ws": workspace_id},
        )
    ).first()
    if row is None or not row.chunk_table:
        return BASE_CHUNK_TABLE, None
    return validate_chunk_table(str(row.chunk_table)), row.id


async def _insert_document(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    document_id: UUID,
    parsed: ParsedDocument,
    raw_hash: str,
    size: int,
    mtime: datetime | None,
    indexed_at: datetime,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO documents (
              id, workspace_id, path, title, content, frontmatter_json,
              content_hash, raw_hash, size, mtime, indexed_at, tags, extension,
              search_text, occurred_on, source_type, parser_version, tokenizer_version,
              parse_warnings
            ) VALUES (
              :id, :ws, :path, :title, :content, CAST(:frontmatter AS jsonb),
              :content_hash, :raw_hash, :size, :mtime, :indexed_at, :tags, :extension,
              :search_text, :occurred_on, 'user', :parser_version, :tokenizer_version,
              CAST(:parse_warnings AS jsonb)
            )
            ON CONFLICT (workspace_id, path) DO UPDATE SET
              title = EXCLUDED.title,
              content = EXCLUDED.content,
              frontmatter_json = EXCLUDED.frontmatter_json,
              content_hash = EXCLUDED.content_hash,
              raw_hash = EXCLUDED.raw_hash,
              size = EXCLUDED.size,
              mtime = EXCLUDED.mtime,
              indexed_at = EXCLUDED.indexed_at,
              tags = EXCLUDED.tags,
              extension = EXCLUDED.extension,
              search_text = EXCLUDED.search_text,
              occurred_on = EXCLUDED.occurred_on,
              parser_version = EXCLUDED.parser_version,
              tokenizer_version = EXCLUDED.tokenizer_version,
              parse_warnings = EXCLUDED.parse_warnings
            """
        ),
        {
            "id": document_id,
            "ws": workspace_id,
            "path": parsed.path,
            "title": parsed.title,
            "content": parsed.body,
            "frontmatter": json.dumps(
                parsed.frontmatter, ensure_ascii=False, default=json_default
            ),
            "content_hash": parsed.content_hash,
            "raw_hash": raw_hash,
            "size": size,
            "mtime": mtime,
            "indexed_at": indexed_at,
            "tags": parsed.tags,
            "extension": parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else "md",
            "search_text": parsed.search_text,
            "occurred_on": parsed.occurred_on,
            "parser_version": parsed.parser_version,
            "tokenizer_version": parsed.tokenizer_version,
            "parse_warnings": json.dumps(
                parsed.parse_warnings, ensure_ascii=False, default=json_default
            ),
        },
    )


async def _replace_chunks(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    document_id: UUID,
    parsed: ParsedDocument,
    embedder: EmbeddingProvider | None,
    outcome: IngestOutcome,
    table: str = "document_chunks",
    profile_id: UUID | None = None,
) -> None:
    validate_chunk_table(table)
    await session.execute(
        text(f"DELETE FROM {table} WHERE document_id = :doc"), {"doc": document_id}
    )
    inserted: list[tuple[UUID, str]] = []
    rows: list[dict[str, object]] = []
    for chunk in parsed.chunks:
        chunk_id = uuid7()
        rows.append(
            {
                "id": chunk_id,
                "ws": workspace_id,
                "doc": document_id,
                "heading": chunk.heading,
                "content": chunk.content,
                "tokens": max(1, len(chunk.content) // 2),
                "ordinal": chunk.ordinal,
                "hash": chunk.chunk_hash,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
            }
        )
        inserted.append((chunk_id, chunk.content))

    if rows:
        # executemany：5k 文档 / 40k chunk 时把逐行往返降到批处理。
        await session.execute(
            text(
                f"""
                INSERT INTO {table} (
                  id, workspace_id, document_id, heading, content, token_count,
                  chunk_ordinal, chunk_hash, line_start, line_end, embedding_profile_id
                ) VALUES (
                  :id, :ws, :doc, :heading, :content, :tokens,
                  :ordinal, :hash, :line_start, :line_end, :profile
                )
                """
            ),
            [{**row, "profile": profile_id} for row in rows],
        )

    if not inserted or embedder is None:
        return
    try:
        vectors = await embedder.embed([content for _, content in inserted])
    except EmbeddingUnavailable:
        outcome.degraded = True
        return
    updates = [
        {
            "id": chunk_id,
            "vector": "[" + ",".join(f"{value:.8f}" for value in vector) + "]",
        }
        for (chunk_id, _), vector in zip(inserted, vectors, strict=True)
    ]
    await session.execute(
        text(f"UPDATE {table} SET embedding = CAST(:vector AS vector) WHERE id = :id"),
        updates,
    )


async def _replace_links(
    session: AsyncSession, *, workspace_id: UUID, document_id: UUID, parsed: ParsedDocument
) -> None:
    await session.execute(
        text("DELETE FROM document_links WHERE source_document_id = :doc"), {"doc": document_id}
    )
    for link in parsed.links:
        await session.execute(
            text(
                "INSERT INTO document_links "
                "(id, workspace_id, source_document_id, target_path, link_text) "
                "VALUES (:id, :ws, :doc, :target, :text) "
                "ON CONFLICT (source_document_id, target_path, link_text) DO NOTHING"
            ),
            {
                "id": uuid7(),
                "ws": workspace_id,
                "doc": document_id,
                "target": link.target_path,
                "text": link.link_text,
            },
        )


async def _record_change(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    document_id: UUID,
    change_type: str,
    source_date: date | None,
    commit_sha: str | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO document_changes "
            "(id, workspace_id, document_id, change_type, source_date, commit_sha) "
            "VALUES (:id, :ws, :doc, :change, :date, :sha)"
        ),
        {
            "id": uuid7(),
            "ws": workspace_id,
            "doc": document_id,
            "change": change_type,
            "date": source_date,
            "sha": commit_sha,
        },
    )


async def _insert_snapshot(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    commit_sha: str | None,
    manifest: list[tuple[str, str]],
    storage_path: str,
) -> UUID:
    manifest_digest = hashlib.sha256(
        "\n".join(f"{path}:{hash_value}" for path, hash_value in sorted(manifest)).encode("utf-8")
    ).hexdigest()
    snapshot_id = uuid7()
    result = await session.execute(
        text(
            """
            INSERT INTO source_snapshots
              (id, workspace_id, commit_sha, manifest_digest, doc_count, as_of, storage_path)
            VALUES (:id, :ws, :sha, :digest, :count, now(), :storage)
            ON CONFLICT (workspace_id, commit_sha) DO UPDATE
              SET manifest_digest = EXCLUDED.manifest_digest
            RETURNING id
            """
        ),
        {
            "id": snapshot_id,
            "ws": workspace_id,
            "sha": commit_sha,
            "digest": manifest_digest,
            "count": len(manifest),
            "storage": storage_path,
        },
    )
    snapshot_id = result.scalar_one()
    return snapshot_id


async def ingest_files(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    files: list[SourceFile],
    commit_sha: str | None,
    storage_path: str,
    source_date: date | None = None,
    embedder: EmbeddingProvider | None = None,
    job_id: UUID | None = None,
) -> IngestOutcome:
    outcome = IngestOutcome()
    # 蓝绿换维后写入必须落到 active profile 的表（PHASE5-SPEC D8）。
    table, profile_id = await _active_chunk_target(session, workspace_id)
    existing = await _load_documents(session, workspace_id)
    current_paths = {file.path for file in files}
    deleted_candidates = {
        row["content_hash"]: row for path, row in existing.items() if path not in current_paths
    }
    reused_ids: set[UUID] = set()

    manifest: list[tuple[str, str]] = []
    snapshot_rows: list[dict[str, object]] = []
    processed = 0
    total = len(files)

    for source in files:
        processed += 1
        path = source.path
        raw_hash = _content_hash(source.data)
        row = existing.get(path)

        if row and row.get("raw_hash") == raw_hash:
            outcome.counters["unchanged"] += 1
            manifest.append((path, row["content_hash"]))
            snapshot_rows.append(
                {"path": path, "hash": row["content_hash"], "doc": row["id"]}
            )
            continue

        try:
            text_content = _decode(source.data)
            parsed = parse_document(path, text_content, parser_version=PARSER_VERSION)
        except UnicodeDecodeError:
            outcome.counters["failed"] += 1
            outcome.failures.append(
                {
                    "path": path,
                    "line": None,
                    "reason_code": "ENCODING_ERROR",
                    "message": "无法以 UTF-8/GB18030 解码",
                }
            )
            continue
        except ParseError as exc:
            outcome.counters["failed"] += 1
            outcome.failures.append(
                {
                    "path": path,
                    "line": exc.line,
                    "reason_code": exc.reason_code,
                    "message": exc.message,
                }
            )
            continue

        manifest.append((path, parsed.content_hash))

        if row and row["content_hash"] == parsed.content_hash:
            # 内容语义未变，仅原始字节变化：不动 indexed_at，也不重算 chunk/embedding。
            await session.execute(
                text(
                    "UPDATE documents SET raw_hash = :raw, size = :size, mtime = :mtime "
                    "WHERE id = :id"
                ),
                {
                    "raw": raw_hash,
                    "size": len(source.data),
                    "mtime": source.mtime,
                    "id": row["id"],
                },
            )
            outcome.counters["unchanged"] += 1
            continue

        renamed = False
        if row is not None:
            document_id = row["id"]
            change_type = "modified"
        else:
            candidate = deleted_candidates.pop(parsed.content_hash, None)
            if candidate is not None:
                document_id = candidate["id"]
                reused_ids.add(document_id)
                await session.execute(
                    text("UPDATE documents SET path = :path WHERE id = :id"),
                    {"path": path, "id": document_id},
                )
                renamed = True
                change_type = "renamed"
            else:
                document_id = uuid7()
                change_type = "added"

        await _insert_document(
            session,
            workspace_id=workspace_id,
            document_id=document_id,
            parsed=parsed,
            raw_hash=raw_hash,
            size=len(source.data),
            mtime=source.mtime,
            indexed_at=datetime.now().astimezone(),
        )
        snapshot_rows.append(
            {"path": path, "hash": parsed.content_hash, "doc": document_id}
        )

        if renamed:
            # 重命名：保留 document_id、chunk 与 embedding，只更新正文相关字段。
            outcome.counters["updated"] += 1
        else:
            await _replace_chunks(
                session,
                workspace_id=workspace_id,
                document_id=document_id,
                parsed=parsed,
                embedder=embedder,
                outcome=outcome,
                table=table,
                profile_id=profile_id,
            )
            await _replace_links(
                session, workspace_id=workspace_id, document_id=document_id, parsed=parsed
            )
            outcome.counters["updated" if row else "created"] += 1

        await _record_change(
            session,
            workspace_id=workspace_id,
            document_id=document_id,
            change_type=change_type,
            source_date=source_date or parsed.occurred_on,
            commit_sha=commit_sha,
        )

        # Todo 台账：重导入 delete+reinsert（AC-32）。
        if parsed.occurred_on is None and parsed.task_occurrences:
            outcome.counters["tasks_skipped_no_date"] += 1
        written = await sync_document_occurrences(
            session,
            workspace_id=workspace_id,
            document_id=document_id,
            occurred_on=parsed.occurred_on,
            occurrences=parsed.task_occurrences,
        )
        outcome.counters["task_occurrences"] += written
        for warning in parsed.parse_warnings:
            if warning.get("code") == "DATE_SOURCE_CONFLICT":
                outcome.counters["date_conflicts"] += 1

        if job_id is not None and (processed % 10 == 0 or processed == total):
            await _update_progress(
                session,
                workspace_id,
                job_id=job_id,
                total=total,
                processed=processed,
                outcome=outcome,
            )

    # 删除本次快照中不存在的文档（未被重命名复用）
    remaining = {path: row for path, row in existing.items() if path not in current_paths}
    for _path, row in remaining.items():
        if row["id"] in reused_ids:
            continue
        # 先删 occurrence 再删文档（task_occurrences.document_id 无外键）。
        await delete_document_occurrences(
            session, workspace_id=workspace_id, document_id=row["id"]
        )
        await session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": row["id"]})
        await _record_change(
            session,
            workspace_id=workspace_id,
            document_id=row["id"],
            change_type="deleted",
            source_date=source_date,
            commit_sha=commit_sha,
        )
        outcome.counters["updated"] += 1

    # 台账重建：tasks 完全由 occurrence 派生（AC-32）。
    await rebuild_tasks(session, workspace_id=workspace_id)

    snapshot_id = await _insert_snapshot(
        session,
        workspace_id=workspace_id,
        commit_sha=commit_sha,
        manifest=manifest,
        storage_path=storage_path,
    )
    if snapshot_rows:
        await session.execute(
            text(
                "INSERT INTO snapshot_documents "
                "(id, workspace_id, snapshot_id, document_id, path, content_hash) "
                "VALUES (:id, :ws, :snap, :doc, :path, :hash) "
                "ON CONFLICT (snapshot_id, document_id) DO NOTHING"
            ),
            [
                {
                    "id": uuid7(),
                    "ws": workspace_id,
                    "snap": snapshot_id,
                    "doc": item["doc"],
                    "path": item["path"],
                    "hash": item["hash"],
                }
                for item in snapshot_rows
            ],
        )
    outcome.snapshot_id = snapshot_id
    outcome.manifest_digest = hashlib.sha256(
        "\n".join(f"{path}:{hash_value}" for path, hash_value in sorted(manifest)).encode("utf-8")
    ).hexdigest()
    outcome.counters["skipped"] = max(0, total - processed)
    return outcome


async def _update_progress(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    job_id: UUID,
    total: int,
    processed: int,
    outcome: IngestOutcome,
) -> None:
    progress = {
        "total_files": total,
        "processed_files": processed,
        "failed_files": outcome.counters["failed"],
        "skipped_files": outcome.counters["skipped"],
    }
    await session.execute(
        text(
            "UPDATE job_runs SET progress_json = CAST(:progress AS jsonb), "
            "counters_json = CAST(:counters AS jsonb), "
            "failed_items_json = CAST(:failed AS jsonb) "
            "WHERE id = :job AND workspace_id = :ws"
        ),
        {
            "progress": json.dumps(progress),
            "counters": json.dumps(outcome.counters),
            "failed": json.dumps(outcome.failures[:200], ensure_ascii=False),
            "job": job_id,
            "ws": workspace_id,
        },
    )
    # 分批提交：进度对 API 可见，同时避免整批 200MB 导入长期占用一个事务。
    await session.commit()
    await bind_workspace(session, workspace_id)
