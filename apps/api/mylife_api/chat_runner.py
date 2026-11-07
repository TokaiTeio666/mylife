"""Chat 精简分支编排（ARCHITECTURE §4.6）。

CREATED → RETRIEVING（有界工具循环）→ DRAFTING（流式）→ VERIFYING（引用闭包）
→ SUCCEEDED / FAILED / CANCELLED。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from mylife_agent.citations import close_citations
from mylife_agent.injection import scan_injections
from mylife_agent.llm import (
    CHAT_NO_EVIDENCE,
    EvidenceQuote,
    LLMKeyMissing,
    LLMRequest,
    LLMUnavailable,
    build_llm_provider,
)
from mylife_agent.prompt import build_messages, plan_tool_calls, should_read_note
from mylife_contract.errors import ErrorCode
from mylife_contract.search import SearchResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mylife_api import repo_chat as repo
from mylife_api.db import get_session_factory, set_rls_context
from mylife_api.runbus import bus
from mylife_api.settings import settings
from mylife_api.tools import ToolContext, execute_tool

logger = logging.getLogger("mylife.api.chat")

TERMINAL_STATES = {"succeeded", "failed", "cancelled", "security_abort"}
ACTIVE_STATES = {"created", "retrieving", "drafting", "verifying"}
TURN_TIMEOUT_SECONDS = 60.0
MAX_TOOL_CALLS = 10
MAX_QUOTE_CHARS = 1200


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _summarize_args(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "search_notes":
        return f'query="{args.get("query", "")}" top_k={args.get("top_k", 20)}'
    if tool_name == "read_note":
        target = args.get("path") or args.get("document_id")
        heading = args.get("heading")
        return f"target={target}" + (f' heading="{heading}"' if heading else "")
    if tool_name == "list_tasks":
        return f'status={args.get("status") or "all"} limit={args.get("limit", 50)}'
    if tool_name == "get_review":
        return f'kind={args.get("kind")} period={args.get("period_key")}'
    if tool_name == "get_stats":
        return f'metric={args.get("metric")} from={args.get("from")} to={args.get("to")}'
    if tool_name == "create_review":
        return f'kind={args.get("kind")} period={args.get("period_key")}'
    return ""


@dataclass(slots=True)
class Candidate:
    item: SearchResult


class ChatRunner:
    def __init__(self, run_id: UUID, workspace_id: UUID) -> None:
        self.run_id = run_id
        self.workspace_id = workspace_id
        self.user_id: UUID | None = None
        self.conversation_id: UUID | None = None
        self.assistant_message_id: UUID | None = None
        self.user_message_id: UUID | None = None
        self.question = ""
        self.state = "created"
        self.tool_events: list[dict[str, Any]] = []
        self.started = time.monotonic()

    # ------------------------------------------------------------------ 事件
    async def _event(
        self,
        session: AsyncSession,
        run: dict[str, Any],
        event: str,
        payload: dict[str, Any],
        *,
        from_state: str | None = None,
        to_state: str | None = None,
    ) -> int:
        event_id = await repo.insert_event(
            session,
            workspace_id=self.workspace_id,
            run_id=self.run_id,
            event=event,
            payload=payload,
            from_state=from_state,
            to_state=to_state,
        )
        await session.commit()
        await set_rls_context(session, self.workspace_id, self.user_id)
        await bus.publish(
            self.run_id, {"id": event_id, "event": event, "data": payload}
        )
        return event_id

    async def _set_state(
        self,
        session: AsyncSession,
        run: dict[str, Any],
        to_state: str,
        *,
        finished: bool = False,
    ) -> None:
        from_state = run.get("state")
        await repo.update_run_state(
            session,
            workspace_id=self.workspace_id,
            run_id=self.run_id,
            state=to_state,
            finished=finished,
        )
        run["state"] = to_state
        self.state = to_state
        await self._event(
            session,
            run,
            "run.state",
            {
                "from_state": from_state,
                "to_state": to_state,
                "attempt_id": str(run.get("attempt_id") or ""),
                "at": datetime.now(UTC).isoformat(),
            },
            from_state=from_state,
            to_state=to_state,
        )

    async def _publish_token(self, delta: str, seq: int) -> None:
        await bus.publish(
            self.run_id,
            {
                "id": None,
                "event": "token",
                "data": {
                    "delta": delta,
                    "message_id": str(self.assistant_message_id or ""),
                    "seq": seq,
                },
            },
        )

    # ------------------------------------------------------------------ 主流程
    async def run(self) -> None:
        try:
            await self._run()
        except Exception:  # noqa: BLE001 - 运行边界必须落为 failed
            logger.exception("chat run crashed", extra={"run_id": str(self.run_id)})
            await self._hard_fail()

    async def _hard_fail(self) -> None:
        factory = get_session_factory()
        try:
            async with factory() as session:
                await set_rls_context(session, self.workspace_id, self.user_id)
                run = await repo.get_run(
                    session, workspace_id=self.workspace_id, run_id=self.run_id
                )
                if run is None or run["state"] in TERMINAL_STATES:
                    return
                if self.assistant_message_id:
                    await repo.finalize_assistant_message(
                        session,
                        message_id=self.assistant_message_id,
                        content="",
                        status="partial",
                        references=[],
                        tool_events=self.tool_events,
                        usage=None,
                    )
                await self._set_state(session, run, "failed", finished=True)
                await self._event(
                    session,
                    run,
                    "chat.error",
                    {
                        "code": ErrorCode.INTERNAL_ERROR.value,
                        "message": "对话运行失败",
                        "state": "failed",
                        "retryable": True,
                    },
                )
        except Exception:  # pragma: no cover - 最后兜底
            logger.exception("failed to mark chat run failed")

    async def _run(self) -> None:
        factory: async_sessionmaker[AsyncSession] = get_session_factory()
        async with factory() as session:
            await set_rls_context(session, self.workspace_id, None)
            run = await repo.get_run(
                session, workspace_id=self.workspace_id, run_id=self.run_id
            )
            if run is None or run["state"] != "created":
                return

            payload = run.get("state_payload") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            self.user_id = UUID(payload["user_id"])
            self.conversation_id = run["conversation_id"]
            self.assistant_message_id = UUID(payload["assistant_message_id"])
            if payload.get("user_message_id"):
                self.user_message_id = UUID(payload["user_message_id"])
            self.question = payload["question"]
            options = payload.get("options") or {}
            await set_rls_context(session, self.workspace_id, self.user_id)

            await self._set_state(session, run, "retrieving")
            if await self._cancelled(session):
                return

            evidence, stop = await self._retrieve(session, run, options)
            if stop:
                return

            # 无证据：直接用哨兵回答，仍走 DRAFTING/VERIFYING/SUCCEEDED。
            if await self._cancelled(session):
                return
            await self._set_state(session, run, "drafting")
            answer, usage, failed_partial = await self._draft(session, run, evidence)
            if failed_partial:
                if await self._cancelled(session):
                    return
                await self._finish_failure(session, run, answer, usage)
                return

            await self._set_state(session, run, "verifying")
            if await self._cancelled(session):
                return
            await self._verify_and_finish(session, run, answer, usage, evidence)

    async def _cancelled(self, session: AsyncSession) -> bool:
        row = await repo.get_run(
            session, workspace_id=self.workspace_id, run_id=self.run_id
        )
        if row is not None and row["state"] in TERMINAL_STATES:
            if self.assistant_message_id and row["state"] == "cancelled":
                await repo.update_message_status(
                    session, self.assistant_message_id, "cancelled"
                )
                await session.commit()
                await set_rls_context(session, self.workspace_id, self.user_id)
            return True
        return False

    # ------------------------------------------------------------------ 取证
    async def _retrieve(
        self, session: AsyncSession, run: dict[str, Any], options: dict[str, Any]
    ) -> tuple[list[EvidenceQuote], bool]:
        top_k = int(options.get("retrieval_top_k") or 10)
        context_window = int(options.get("context_window_tokens") or 32_000)
        ctx = ToolContext(
            workspace_id=self.workspace_id,
            user_id=self.user_id or self.workspace_id,
            run_id=self.run_id,
            role="owner",
            trace_id=run.get("trace_id") or "",
            run_kind="chat",
        )

        calls = plan_tool_calls(self.question, top_k=top_k)
        search_results: list[SearchResult] = []
        tool_call_count = 0

        for call in calls:
            if tool_call_count >= MAX_TOOL_CALLS:
                break
            if self.started and (time.monotonic() - self.started) > TURN_TIMEOUT_SECONDS:
                break
            if await self._cancelled(session):
                return [], True
            tool_call_count += 1
            execution = await self._run_tool(session, run, ctx, call)
            if call["tool_name"] == "search_notes" and execution.status == "ok":
                result = execution.result
                if hasattr(result, "items"):
                    search_results.extend(result.items)

        if (
            should_read_note(self.question, bool(search_results))
            and tool_call_count < MAX_TOOL_CALLS
        ):
            if await self._cancelled(session):
                return [], True
            target = search_results[0]
            await self._run_tool(
                session,
                run,
                ctx,
                {
                    "tool_name": "read_note",
                    "args": {"document_id": str(target.document_id), "window": 200},
                },
            )

        return await self._build_evidence(
            session, search_results, top_k=top_k, context_window=context_window
        ), False

    async def _run_tool(
        self,
        session: AsyncSession,
        run: dict[str, Any],
        ctx: ToolContext,
        call: dict[str, Any],
    ):
        tool_name = call["tool_name"]
        raw_args = call["args"]
        seq = await repo.next_step_seq(session, self.run_id)
        args_digest = _digest(raw_args)
        await self._event(
            session,
            run,
            "chat.tool_call",
            {
                "seq": seq,
                "role": "worker",
                "tool_name": tool_name,
                "args_digest": args_digest,
                "args_summary": _summarize_args(tool_name, raw_args),
                "at": datetime.now(UTC).isoformat(),
            },
        )

        execution = await execute_tool(session, ctx, tool_name, raw_args)

        step_status = {
            "ok": "succeeded",
            "error": "failed",
            "timeout": "failed",
            "skipped": "skipped",
        }[execution.status]
        await repo.insert_step(
            session,
            workspace_id=self.workspace_id,
            run_id=self.run_id,
            seq=seq,
            role="worker",
            tool_name=tool_name,
            status=step_status,
            input_digest=execution.args_digest,
            output_digest=execution.result_digest,
            latency_ms=execution.latency_ms,
        )
        event_payload = {
            "seq": seq,
            "status": execution.status,
            "latency_ms": execution.latency_ms,
            "result_digest": execution.result_digest,
            "result_summary": execution.summary,
        }
        if execution.status == "ok":
            self.tool_events.append(
                {
                    "seq": seq,
                    "tool_name": tool_name,
                    "args_digest": execution.args_digest,
                    "args_summary": _summarize_args(tool_name, raw_args),
                    "status": "ok",
                    "latency_ms": execution.latency_ms,
                }
            )
        else:
            self.tool_events.append(
                {
                    "seq": seq,
                    "tool_name": tool_name,
                    "args_digest": execution.args_digest,
                    "args_summary": _summarize_args(tool_name, raw_args),
                    "status": "error",
                    "latency_ms": execution.latency_ms,
                }
            )
        await self._event(session, run, "chat.tool_result", event_payload)
        return execution

    async def _build_evidence(
        self,
        session: AsyncSession,
        results: list[SearchResult],
        *,
        top_k: int,
        context_window: int,
    ) -> list[EvidenceQuote]:
        seen: set[UUID] = set()
        deduped: list[SearchResult] = []
        for item in results:
            if item.chunk_id is None or item.chunk_id in seen:
                continue
            seen.add(item.chunk_id)
            deduped.append(item)

        chunk_ids = [item.chunk_id for item in deduped if item.chunk_id]
        contents = await repo.get_chunk_contents(session, chunk_ids)

        evidence: list[EvidenceQuote] = []
        used_tokens = 0
        trimmed = False
        for item in deduped:
            if len(evidence) >= top_k:
                trimmed = True
                break
            chunk = contents.get(item.chunk_id) if item.chunk_id else None
            raw_content = (chunk or {}).get("content") or item.snippet or ""
            hits = scan_injections(raw_content)
            if hits:
                await self._event(
                    session,
                    {"state": self.state},
                    "security.prompt_injection",
                    {
                        "path": item.path,
                        "document_id": str(item.document_id or ""),
                        "kinds": [hit.kind for hit in hits],
                    },
                )
                continue
            quote = (item.snippet or raw_content)[:MAX_QUOTE_CHARS]
            estimate = max(1, len(quote) // 3)
            if evidence and used_tokens + estimate > context_window:
                trimmed = True
                break
            used_tokens += estimate
            evidence.append(
                EvidenceQuote(
                    evidence_id=f"e{len(evidence) + 1}",
                    path=item.path,
                    title=item.title,
                    heading=item.heading,
                    quote=quote,
                    line_range=(
                        (item.line_range[0], item.line_range[1])
                        if item.line_range and len(item.line_range) == 2
                        else None
                    ),
                    document_id=str(item.document_id) if item.document_id else None,
                    chunk_id=str(item.chunk_id) if item.chunk_id else None,
                )
            )
        if trimmed:
            await self._event(
                session,
                {"state": self.state},
                "context.trimmed",
                {"limit_tokens": context_window, "kept": len(evidence)},
            )
        return evidence

    # ------------------------------------------------------------------ 生成
    async def _history(self, session: AsyncSession) -> list[tuple[str, str]]:
        if self.conversation_id is None:
            return []
        rows = await repo.list_messages(
            session, conversation_id=self.conversation_id, limit=50
        )
        history: list[tuple[str, str]] = []
        for row in rows:
            if row["id"] in {
                self.assistant_message_id,
                self.user_message_id,
            }:
                break
            if row["role"] in {"user", "assistant"} and row["status"] == "complete":
                history.append((row["role"], (row["content"] or "")[:1500]))
        return history[-6:]

    async def _draft(
        self, session: AsyncSession, run: dict[str, Any], evidence: list[EvidenceQuote]
    ) -> tuple[str, dict[str, Any] | None, bool]:
        history = await self._history(session)
        system_prompt, user_content = build_messages(self.question, evidence, history)
        request = LLMRequest(
            question=user_content,
            evidence=evidence,
            history=history,
            system_prompt=system_prompt,
        )

        if not evidence:
            answer = CHAT_NO_EVIDENCE
            for seq, start in enumerate(range(0, len(answer), 6), start=1):
                await self._publish_token(answer[start : start + 6], seq)
            usage = {
                "prompt_tokens": max(1, len(self.question) // 3),
                "completion_tokens": max(1, len(answer) // 3),
                "model": "sentinel",
                "latency_ms": 0,
            }
            return answer, usage, False

        try:
            provider = build_llm_provider(
                provider=settings.llm_provider,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
            )
        except LLMKeyMissing:
            await self._event(
                session,
                run,
                "chat.error",
                {
                    "code": ErrorCode.PROVIDER_KEY_MISSING.value,
                    "message": "尚未配置模型凭据",
                    "state": self.state,
                    "retryable": False,
                },
            )
            return "", None, True

        parts: list[str] = []
        seq = 0
        try:
            async for delta in provider.stream(request):
                parts.append(delta)
                seq += 1
                await self._publish_token(delta, seq)
                if seq % 20 == 0:
                    if await self._cancelled(session):
                        return "".join(parts), None, True
                    if (time.monotonic() - self.started) > TURN_TIMEOUT_SECONDS:
                        raise LLMUnavailable("turn timeout")
        except LLMUnavailable:
            usage_obj = provider.usage
            usage = (
                {
                    "prompt_tokens": usage_obj.prompt_tokens,
                    "completion_tokens": usage_obj.completion_tokens,
                    "model": usage_obj.model,
                    "latency_ms": usage_obj.latency_ms,
                }
                if usage_obj
                else None
            )
            return "".join(parts), usage, True

        usage_obj = provider.usage
        usage = (
            {
                "prompt_tokens": usage_obj.prompt_tokens,
                "completion_tokens": usage_obj.completion_tokens,
                "model": usage_obj.model,
                "latency_ms": usage_obj.latency_ms,
            }
            if usage_obj
            else None
        )
        return "".join(parts), usage, False

    # ------------------------------------------------------------------ 收尾
    async def _verify_and_finish(
        self,
        session: AsyncSession,
        run: dict[str, Any],
        answer: str,
        usage: dict[str, Any] | None,
        evidence: list[EvidenceQuote],
    ) -> None:
        closure = close_citations(answer, len(evidence))
        references: list[dict[str, Any]] = []
        for new_index, original in enumerate(closure.used_indexes, start=1):
            quote = evidence[original - 1]
            if not quote.document_id or not quote.chunk_id:
                continue
            references.append(
                {
                    "index": new_index,
                    "document_id": quote.document_id,
                    "chunk_id": quote.chunk_id,
                    "path": quote.path,
                    "title": quote.title,
                    "heading": quote.heading,
                    "line_range": list(quote.line_range) if quote.line_range else None,
                    "snippet": quote.quote,
                }
            )

        final_answer = closure.answer
        if not answer.strip() or not evidence:
            final_answer = CHAT_NO_EVIDENCE
            references = []
        # 引用序号必须与正文一致：若部分引用缺 id 被丢弃，正文序号需保持连续。
        references = [
            {**reference, "index": index}
            for index, reference in enumerate(references, start=1)
        ]

        if self.assistant_message_id:
            await repo.finalize_assistant_message(
                session,
                message_id=self.assistant_message_id,
                content=final_answer,
                status="complete",
                references=references,
                tool_events=self.tool_events,
                usage=usage,
            )
        if usage is not None and self.user_id is not None:
            await repo.insert_usage(
                session,
                workspace_id=self.workspace_id,
                user_id=self.user_id,
                model=usage.get("model", "unknown"),
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                latency_ms=int(usage.get("latency_ms", 0)),
                degraded=False,
            )
        await session.commit()
        await set_rls_context(session, self.workspace_id, self.user_id)

        for reference in references:
            await self._event(
                session,
                run,
                "chat.citation",
                {
                    "index": reference["index"],
                    "evidence_id": f"e{reference['index']}",
                    "document_id": reference["document_id"],
                    "chunk_id": reference["chunk_id"],
                    "path": reference["path"],
                    "heading": reference["heading"],
                    "line_range": reference["line_range"],
                },
            )

        await self._set_state(session, run, "succeeded", finished=True)
        await self._event(
            session,
            run,
            "chat.done",
            {
                "state": "succeeded",
                "message_id": str(self.assistant_message_id or ""),
                "review_id": None,
                "usage": usage,
            },
        )

    async def _finish_failure(
        self,
        session: AsyncSession,
        run: dict[str, Any],
        answer: str,
        usage: dict[str, Any] | None,
    ) -> None:
        if self.assistant_message_id:
            await repo.finalize_assistant_message(
                session,
                message_id=self.assistant_message_id,
                content=answer,
                status="partial",
                references=[],
                tool_events=self.tool_events,
                usage=usage,
            )
        await session.commit()
        await set_rls_context(session, self.workspace_id, self.user_id)
        await self._set_state(session, run, "failed", finished=True)
        await self._event(
            session,
            run,
            "chat.error",
            {
                "code": ErrorCode.LLM_UNAVAILABLE.value,
                "message": "模型响应中断，已保留已生成内容",
                "state": "failed",
                "retryable": True,
            },
        )

async def run_chat(run_id: UUID, workspace_id: UUID) -> None:
    runner = ChatRunner(run_id, workspace_id)
    await runner.run()
