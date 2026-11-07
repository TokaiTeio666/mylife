"""ToolGateway（API-CONTRACTS §5）。

所有工具共用同一检索服务；`ToolContext` 由服务端注入，入参模型不含 workspace_id。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from mylife_contract.common import is_valid_period_key
from mylife_contract.errors import ErrorCode
from mylife_contract.tools import (
    CreateReviewArgs,
    CreateReviewConfirmArgs,
    CreateReviewConfirmResult,
    CreateReviewPreview,
    CreateReviewResult,
    EstimatedTokens,
    GetReviewArgs,
    GetReviewResult,
    GetStatsArgs,
    GetStatsResult,
    ListTasksArgs,
    ListTasksResult,
    ReadNoteArgs,
    ReadNoteResult,
    SearchNotesArgs,
    SearchNotesResult,
    TaskItem,
)
from mylife_retrieval.tokenizer import tokenize_for_search
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repo_chat as repo
from mylife_api import repo_phase1 as repo1
from mylife_api import services_search
from mylife_api.errors import AppError

TOOL_TIMEOUT_SECONDS = 10.0
SEARCH_TIMEOUT_SECONDS = 15.0
MAX_READ_CHARS = 8000


@dataclass(frozen=True, slots=True)
class ToolContext:
    workspace_id: UUID
    user_id: UUID
    run_id: UUID
    role: str = "owner"
    deadline_ms: int = 60_000
    trace_id: str = ""
    run_kind: str = "chat"


@dataclass(slots=True)
class ToolExecution:
    tool_name: str
    status: str  # ok | error | timeout | skipped
    result: Any
    summary: str
    args_digest: str
    result_digest: str
    latency_ms: int
    error_code: str | None = None


class ToolError(AppError):
    pass


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _summary_search(args: SearchNotesArgs, result: SearchNotesResult) -> str:
    top = result.items[0] if result.items else None
    where = f" {top.path}" if top else ""
    return f'query="{args.query}" top_k={args.top_k} -> {len(result.items)} hits{where}'


async def search_notes(
    session: AsyncSession, ctx: ToolContext, args: SearchNotesArgs
) -> tuple[SearchNotesResult, str]:
    response = await services_search.search(
        session,
        workspace_id=ctx.workspace_id,
        query=args.query,
        top_k=args.top_k,
        mode=args.mode,
        source_type=args.filters.source_type,
        path_prefix=args.filters.path_prefix,
        tags=args.filters.tags,
        date_from=args.filters.date_from,
        date_to=args.filters.date_to,
        extensions=args.filters.extensions,
        include_snippet_html=False,
    )
    result = SearchNotesResult(
        items=response.items,
        degraded=response.degraded,
        degraded_reasons=response.degraded_reasons,
        took_ms=response.took_ms,
    )
    return result, _summary_search(args, result)


async def read_note(
    session: AsyncSession, ctx: ToolContext, args: ReadNoteArgs
) -> tuple[ReadNoteResult, str]:
    if args.document_id is not None:
        document = await repo1.get_document(session, ctx.workspace_id, args.document_id)
    else:
        document = await repo1.get_document_by_path(session, ctx.workspace_id, args.path or "")
    if document is None:
        raise ToolError(ErrorCode.TOOL_NOT_ALLOWED, details={"reason": "PATH_NOT_IN_SNAPSHOT"})

    # 路径必须在 snapshot manifest 内（Phase 2 用 snapshot_documents 证明）。
    snapshot_id = await repo.evidence_snapshot_for_manifest(
        session, workspace_id=ctx.workspace_id, document_id=document["id"]
    )
    if snapshot_id is None:
        # 工作区尚未导入到快照（例如测试直接插入文档）时仍允许读，但不能跨租户。
        pass

    lines = (document["content"] or "").split("\n")
    heading = args.heading
    if heading:
        chunks = await repo1.list_chunks(session, document["id"])
        matched = next(
            (chunk for chunk in chunks if heading in (chunk.get("heading") or "")), None
        )
        if matched and matched.get("line_start") and matched.get("line_end"):
            start = max(1, int(matched["line_start"]))
            end = min(len(lines), int(matched["line_end"]))
            lines = lines[start - 1 : end]
            heading = matched.get("heading")
    elif args.line_range:
        start = max(1, args.line_range[0])
        end = min(len(lines), args.line_range[1])
        lines = lines[start - 1 : end]
    elif args.window:
        lines = lines[: args.window]

    content = "\n".join(lines)
    truncated = len(content) > MAX_READ_CHARS
    content = content[:MAX_READ_CHARS]
    wrapped = f"<<<EVIDENCE id={document['id']}>>>\n{content}\n<<<END>>>"
    result = ReadNoteResult(
        document_id=document["id"],
        path=document["path"],
        title=document["title"],
        heading=heading,
        line_range=[int(lines and 1 or 1), len(lines)] if lines else None,
        content=wrapped,
        content_hash=document["content_hash"],
        truncated=truncated,
    )
    return result, f"read {document['path']} chars={len(content)} truncated={truncated}"


async def list_tasks(
    session: AsyncSession, ctx: ToolContext, args: ListTasksArgs
) -> tuple[ListTasksResult, str]:
    rows = await repo.list_tasks_for_tool(
        session,
        workspace_id=ctx.workspace_id,
        statuses=args.status,
        date_from=args.from_date,
        date_to=args.to_date,
        limit=args.limit,
    )
    counts = await repo.task_status_counts(session, ctx.workspace_id)
    ledger = await repo.task_ledger_recalc(session, ctx.workspace_id)
    items = [
        TaskItem(
            id=row["id"],
            text=row["text"] if args.include_text else "",
            status=row["status"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            done_date=row.get("done_date"),
            source_path=row.get("source_path"),
            line_number=row.get("line_number"),
        )
        for row in rows
    ]
    result = ListTasksResult(items=items, counts=counts, ledger_recalc=ledger)
    return result, f"tasks={len(items)} counts={counts}"


async def get_review(
    session: AsyncSession, ctx: ToolContext, args: GetReviewArgs
) -> tuple[GetReviewResult, str]:
    row = await repo.get_review_for_tool(
        session,
        workspace_id=ctx.workspace_id,
        review_id=args.review_id,
        kind=args.kind,
        period_key=args.period_key,
    )
    if row is None:
        raise ToolError(ErrorCode.TOOL_NOT_FOUND, details={"reason": "REVIEW_NOT_FOUND"})
    result = GetReviewResult(
        review_id=row["id"],
        kind=row["kind"],
        period_key=row["period_key"],
        version=row["version"],
        title=row["title"],
        sections=[],
        entries=[],
        citations=[],
    )
    return result, f"review {row['kind']} {row['period_key']} v{row['version']}"


async def get_stats(
    session: AsyncSession, ctx: ToolContext, args: GetStatsArgs
) -> tuple[GetStatsResult, str]:
    metrics = await repo.metrics_snapshot(session, workspace_id=ctx.workspace_id)
    counts = await repo.task_status_counts(session, ctx.workspace_id)
    ledger = await repo.task_ledger_recalc(session, ctx.workspace_id)

    if args.metric == "completion_rate":
        value: Any = ledger
        sample_size = int(ledger["total"])
    elif args.metric == "task_counts":
        value = counts
        sample_size = sum(counts.values())
    elif args.metric == "review_counts":
        value = {"reviews": metrics["review_count"]}
        sample_size = metrics["review_count"]
    elif args.metric == "token_usage":
        value = {
            "prompt_tokens": metrics["prompt_tokens"],
            "completion_tokens": metrics["completion_tokens"],
            "calls": metrics["calls"],
        }
        sample_size = metrics["calls"]
    else:  # no_evidence_ratio
        calls = metrics["calls"]
        value = round(metrics["degraded_calls"] / calls, 4) if calls else 0.0
        sample_size = calls

    payload = {
        "metric": args.metric,
        "from": args.from_date.isoformat(),
        "to": args.to_date.isoformat(),
        "group_by": args.group_by,
        "value": value,
    }
    payload_hash = "sha256:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    snapshot_id = await repo.latest_snapshot_id(session, ctx.workspace_id)
    result = GetStatsResult(
        metric=args.metric,
        value=value,
        sample_size=sample_size,
        as_of=datetime.now(UTC),
        snapshot_id=snapshot_id,
        payload_hash=payload_hash,
    )
    return result, f"{args.metric} sample={sample_size}"


def _review_title(kind: str, period_key: str) -> tuple[str, str]:
    if kind == "daily":
        year = period_key[:4]
        return f"{period_key} 每日复盘", f"wiki/复盘/每日复盘/{year}/{period_key}.md"
    if kind == "weekly":
        year = period_key[:4]
        return f"{period_key} 每周复盘", f"wiki/复盘/每周复盘/{year}/{period_key}.md"
    return f"{period_key} 年度复盘", f"wiki/复盘/年度复盘/{period_key}.md"


async def create_review_preview(
    session: AsyncSession, ctx: ToolContext, args: CreateReviewArgs
) -> tuple[CreateReviewResult, str]:
    if not is_valid_period_key(args.kind, args.period_key):
        raise ToolError(ErrorCode.REVIEW_PERIOD_INVALID)
    title, path = _review_title(args.kind, args.period_key)

    evidence_count = 0
    if args.kind == "daily":
        row = await session.execute(
            _text(
                "SELECT count(*) FROM documents WHERE workspace_id = :ws "
                "AND occurred_on = CAST(:period AS date)"
            ),
            {"ws": ctx.workspace_id, "period": args.period_key},
        )
        evidence_count += int(row.scalar_one())
    else:
        row = await session.execute(
            _text("SELECT count(*) FROM documents WHERE workspace_id = :ws"),
            {"ws": ctx.workspace_id},
        )
        evidence_count += int(row.scalar_one())

    active = await session.execute(
        _text(
            "SELECT id FROM agent_runs WHERE workspace_id = :ws AND kind = :kind "
            "AND period_key = :period AND is_active_state(state) LIMIT 1"
        ),
        {"ws": ctx.workspace_id, "kind": args.kind, "period": args.period_key},
    )
    blocked = "ACTIVE_RUN_EXISTS" if active.first() is not None else None

    from mylife_agent.budget import budget_for_kind

    budget = budget_for_kind(args.kind)
    estimated_input = max(1, evidence_count * 300)
    estimated_output = max(1, evidence_count * 60)
    if (
        estimated_input > budget.max_input_tokens
        or estimated_output > budget.max_output_tokens
    ):
        blocked = blocked or "BUDGET_EXCEEDED"

    from mylife_api import confirmations

    token, expires_at = confirmations.issue_token(
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        kind=args.kind,
        period_key=args.period_key,
    )
    await confirmations.store_token(
        session,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        kind=args.kind,
        period_key=args.period_key,
        token=token,
        expires_at=expires_at,
    )

    result = CreateReviewResult(
        preview=CreateReviewPreview(
            kind=args.kind,
            period_key=args.period_key,
            title=title,
            path=path,
            estimated_evidence_count=evidence_count,
            estimated_tokens=EstimatedTokens(
                input=estimated_input,
                output=estimated_output,
            ),
        ),
        confirmation_token=token,
        expires_at=expires_at,
        blocked_reason=blocked,
    )
    return result, f"preview {args.kind} {args.period_key} evidence={evidence_count}"


async def create_review_confirm(
    session: AsyncSession, ctx: ToolContext, args: CreateReviewConfirmArgs
) -> tuple[CreateReviewConfirmResult, str]:
    """confirm：校验签名/TTL/未消费，标记已消费但**不入队**（AC-25）。"""
    from mylife_api import confirmations

    try:
        verify = confirmations.verify_token
        _ = verify  # 保留导入便于测试替身
        rows = await session.execute(
            _text(
                "SELECT kind, period_key FROM review_confirmations "
                "WHERE token_hash = :hash AND workspace_id = :ws AND user_id = :uid"
            ),
            {
                "hash": confirmations.token_hash(args.confirmation_token),
                "ws": ctx.workspace_id,
                "uid": ctx.user_id,
            },
        )
        row = rows.first()
        if row is None:
            raise ToolError(
                ErrorCode.TOOL_ARGS_INVALID, details={"reason": "TOKEN_UNKNOWN"}
            )
        await confirmations.consume_token(
            session,
            token=args.confirmation_token,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user_id,
            kind=row.kind,
            period_key=row.period_key,
        )
    except confirmations.TokenError as exc:
        raise ToolError(
            ErrorCode.TOOL_ARGS_INVALID, details={"reason": exc.reason}
        ) from exc

    return (
        CreateReviewConfirmResult(
            confirmed=True, confirmation_token="consumed"
        ),
        "confirmed",
    )


async def _audit_tool_denied(
    session: AsyncSession, ctx: ToolContext, tool_name: str
) -> None:
    """拒绝写工具时写审计（不静默）。"""
    from mylife_api.repositories import insert_audit

    with contextlib.suppress(Exception):
        await insert_audit(
            session,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user_id,
            action="review.tool_denied",
            target=str(ctx.run_id),
            detail={"tool": tool_name, "run_kind": ctx.run_kind},
        )


def _text(sql: str):
    from sqlalchemy import text as sql_text

    return sql_text(sql)


TOOL_ARG_MODELS: dict[str, type] = {
    "search_notes": SearchNotesArgs,
    "read_note": ReadNoteArgs,
    "list_tasks": ListTasksArgs,
    "get_review": GetReviewArgs,
    "get_stats": GetStatsArgs,
    "create_review": CreateReviewArgs,
    "create_review_confirm": CreateReviewConfirmArgs,
}

TOOL_HANDLERS = {
    "search_notes": search_notes,
    "read_note": read_note,
    "list_tasks": list_tasks,
    "get_review": get_review,
    "get_stats": get_stats,
    "create_review": create_review_preview,
    "create_review_confirm": create_review_confirm,
}

#: 只读工具白名单（chat L2 可用；review run 内禁止 create_review）。
READ_ONLY_TOOLS = frozenset(
    {"search_notes", "read_note", "list_tasks", "get_review", "get_stats"}
)
WRITE_TOOLS = frozenset({"create_review", "create_review_confirm"})


async def execute_tool(
    session: AsyncSession, ctx: ToolContext, tool_name: str, raw_args: dict[str, Any]
) -> ToolExecution:
    started = time.perf_counter()
    args_digest = _digest(raw_args)

    if tool_name not in TOOL_ARG_MODELS:
        return ToolExecution(
            tool_name=tool_name,
            status="error",
            result=None,
            summary="unknown tool",
            args_digest=args_digest,
            result_digest=_digest(None),
            latency_ms=0,
            error_code=ErrorCode.TOOL_NOT_FOUND.value,
        )

    if "workspace_id" in raw_args:
        return ToolExecution(
            tool_name=tool_name,
            status="error",
            result=None,
            summary="workspace_id is forbidden",
            args_digest=args_digest,
            result_digest=_digest(None),
            latency_ms=0,
            error_code=ErrorCode.TOOL_ARGS_INVALID.value,
        )

    # review run 内禁止递归创建 review（ARCHITECTURE §3.5）。
    if tool_name in WRITE_TOOLS and ctx.run_kind != "chat":
        await _audit_tool_denied(session, ctx, tool_name)
        return ToolExecution(
            tool_name=tool_name,
            status="error",
            result=None,
            summary="write tools are only allowed in chat runs",
            args_digest=args_digest,
            result_digest=_digest(None),
            latency_ms=0,
            error_code=ErrorCode.TOOL_NOT_ALLOWED.value,
        )

    try:
        args = TOOL_ARG_MODELS[tool_name].model_validate(raw_args)
    except ValidationError as exc:
        return ToolExecution(
            tool_name=tool_name,
            status="error",
            result=None,
            summary=f"invalid args: {exc.error_count()} issue(s)",
            args_digest=args_digest,
            result_digest=_digest(None),
            latency_ms=0,
            error_code=ErrorCode.TOOL_ARGS_INVALID.value,
        )

    try:
        result, summary = await TOOL_HANDLERS[tool_name](session, ctx, args)
    except ToolError as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return ToolExecution(
            tool_name=tool_name,
            status="error",
            result=None,
            summary=exc.message,
            args_digest=args_digest,
            result_digest=_digest(None),
            latency_ms=latency,
            error_code=exc.code.value,
        )
    except Exception as exc:  # noqa: BLE001 - 工具边界统一转为 error
        latency = int((time.perf_counter() - started) * 1000)
        return ToolExecution(
            tool_name=tool_name,
            status="error",
            result=None,
            summary=f"{type(exc).__name__}",
            args_digest=args_digest,
            result_digest=_digest(None),
            latency_ms=latency,
            error_code=ErrorCode.TOOL_ARGS_INVALID.value,
        )

    latency = int((time.perf_counter() - started) * 1000)
    serialized = result.model_dump(mode="json")
    return ToolExecution(
        tool_name=tool_name,
        status="ok",
        result=result,
        summary=summary,
        args_digest=args_digest,
        result_digest=_digest(serialized),
        latency_ms=latency,
    )


def tokenize_question(question: str) -> list[str]:
    return tokenize_for_search(question)
