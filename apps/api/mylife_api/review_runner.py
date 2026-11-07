"""复盘运行时（ARCHITECTURE §4.1–§4.5；PHASE3-SPEC §4）。

`created → snapshotting → planning → retrieving → drafting → reviewing`
→ `delivering → succeeded`，或按门禁进入 `revising` / `human_gate` /
`security_abort` / `failed`。

Phase 3 的 Writer/Reviewer 是确定性装配 + 门禁，不调用生成式 LLM；
预算 guard 仍按 run 计量（chat 与未来 LLM 写作共用同一累计口径）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from mylife_agent.budget import BudgetExceeded, budget_for_kind, evaluate
from mylife_agent.entry_materializer import materialize_entries
from mylife_agent.gates import GateContext
from mylife_agent.reviewer import run_review as run_gates
from mylife_contract.errors import ErrorCode
from mylife_contract.review import EvidenceItem
from mylife_delivery.renderer import render_review_markdown, review_path, topic_path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mylife_api import repo_review as repo
from mylife_api import review_composer, review_evidence, services_budget
from mylife_api.db import get_session_factory, set_rls_context
from mylife_api.review_fsm import TERMINAL_STATES
from mylife_api.runtime import STREAM_EPOCH

logger = logging.getLogger("mylife.api.review")

LEASE_OWNER = f"{socket.gethostname()}:{uuid4().hex[:8]}"
PARSER_VERSION = "mylife-parser-1"
PROMPT_VERSION = "review-deterministic-v1"
MAX_EVIDENCE = 80
MAX_SECTION_EVIDENCE = 12


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class ReviewRunner:
    def __init__(self, run_id: UUID, workspace_id: UUID) -> None:
        self.run_id = run_id
        self.workspace_id = workspace_id
        self.run: dict[str, Any] = {}
        self.payload: dict[str, Any] = {}
        self.review_id: UUID | None = None
        self.version = 1
        self.kind = "daily"
        self.period_key = ""
        self.timezone = "Asia/Shanghai"
        self.degraded_reasons: list[str] = []
        self.warned = False
        self.started = time.monotonic()

    # ------------------------------------------------------------------ 入口
    async def run_from(self, resume_from: str | None = None) -> None:
        factory: async_sessionmaker[AsyncSession] = get_session_factory()
        async with factory() as session:
            await set_rls_context(session, self.workspace_id, None)
            run = await repo.get_run(session, workspace_id=self.workspace_id, run_id=self.run_id)
            if run is None:
                return
            self.run = run
            self.payload = run.get("state_payload") or {}
            if isinstance(self.payload, str):
                self.payload = json.loads(self.payload)
            self.review_id = UUID(self.payload["review_id"])
            self.version = int(run.get("version") or self.payload.get("version") or 1)
            self.kind = run["kind"]
            self.period_key = run["period_key"]
            self.timezone = self.payload.get("timezone") or "Asia/Shanghai"
            await set_rls_context(session, self.workspace_id, self.payload.get("user_id"))

            if run["state"] in TERMINAL_STATES:
                return

            try:
                if run["state"] == "retry_wait":
                    target = run.get("retry_from_state") or "retrieving"
                    ok = await repo.cas_transition(
                        session,
                        workspace_id=self.workspace_id,
                        run_id=self.run_id,
                        from_state="retry_wait",
                        to_state=target,
                        reason="retry",
                        lease_owner=LEASE_OWNER,
                        stream_epoch=STREAM_EPOCH,
                    )
                    if not ok:
                        return
                    run["state"] = target
                    self.run["state"] = target
                if resume_from == "delivering" or run["state"] == "delivering":
                    await self._deliver(session)
                    return
                await self._advance(session, run)
            except BudgetExceeded as exc:
                await self._budget_stop(session, exc)
            except Exception:  # noqa: BLE001 - 运行边界必须落为 failed
                logger.exception("review run crashed", extra={"run_id": str(self.run_id)})
                await self._fail(session, ErrorCode.INTERNAL_ERROR)

    # ------------------------------------------------------------------ 状态
    async def _transition(
        self,
        session: AsyncSession,
        to_state: str,
        reason: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        from_state = self.run.get("state", "created")
        # state_payload 是续跑的唯一真相：每次迁移都持久化当前累计状态。
        if payload is not None:
            self.payload = {**self.payload, **payload}
        ok = await repo.cas_transition(
            session,
            workspace_id=self.workspace_id,
            run_id=self.run_id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            lease_owner=LEASE_OWNER if from_state not in {"created"} else None,
            state_payload=self.payload,
            stream_epoch=STREAM_EPOCH,
        )
        if not ok:
            raise RuntimeError(f"CAS failed {from_state}->{to_state}")
        self.run["state"] = to_state
        await session.commit()
        await set_rls_context(session, self.workspace_id, self.payload.get("user_id"))

    # ------------------------------------------------------------------ 主流程
    async def _advance(self, session: AsyncSession, run: dict[str, Any]) -> None:
        state = run["state"]
        if state == "created":
            await self._transition(session, "snapshotting", "admit_ok")
            state = "snapshotting"
        if state == "snapshotting":
            await self._snapshot(session)
            await self._transition(session, "planning", "snapshot_ok")
            state = "planning"
        if state == "planning":
            plan = self._plan()
            await self._transition(
                session, "retrieving", "plan_valid", payload={**self.payload, "plan": plan}
            )
            state = "retrieving"
        if state == "retrieving":
            bundle = await self._retrieve(session)
            await self._transition(
                session, "drafting", "quorum_ok", payload={**self.payload, "bundle": bundle}
            )
            state = "drafting"
        if state == "drafting":
            await self._draft(session)
            await self._transition(session, "reviewing", "draft_schema_ok")
            state = "reviewing"
        if state == "reviewing":
            await self._review(session)

    # ------------------------------------------------------------------ 阶段
    async def _snapshot(self, session: AsyncSession) -> None:
        snapshot = await repo.latest_snapshot(session, workspace_id=self.workspace_id)
        if snapshot is None:
            # 空 workspace 没有快照是合法空日；只有“有证据却无快照”才算降级，
            # 该判定推迟到取证之后（见 `_retrieve`）。
            self.payload["snapshot_missing"] = True
        else:
            self.payload["snapshot_id"] = str(snapshot["id"])
            self.payload["storage_path"] = snapshot.get("storage_path")

    def _plan(self) -> dict[str, Any]:
        from mylife_contract.constants import SECTION_ORDINALS

        period_start, period_end = self._period_bounds()
        budget = budget_for_kind(self.kind)
        return {
            "plan_version": 1,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "sections": [
                {
                    "section_key": key,
                    "ordinal": ordinal,
                    "title": title,
                    "max_evidence": cap,
                    "required": True,
                }
                for ordinal, key, title, cap in SECTION_ORDINALS[self.kind]
            ],
            "budget": budget.model_dump(),
        }

    def _period_bounds(self) -> tuple[date, date]:
        if self.kind == "daily":
            day = date.fromisoformat(self.period_key)
            return day, day
        if self.kind == "weekly":
            year, week = self.period_key.split("-W")
            start = date.fromisocalendar(int(year), int(week), 1)
            return start, start + timedelta(days=6)
        year = int(self.period_key)
        return date(year, 1, 1), date(year, 12, 31)

    async def _retrieve(self, session: AsyncSession) -> dict[str, Any]:
        collector = review_evidence.EvidenceCollector(
            parser_version=PARSER_VERSION, max_total=MAX_EVIDENCE
        )
        period_start, period_end = self._period_bounds()
        settings_row = await repo0_settings(session, self.workspace_id)
        reviews_root = (settings_row or {}).get("reviews_root") or "wiki/复盘"

        context: dict[str, Any] = {"collector": collector}
        if self.kind == "daily":
            context["period_tasks"] = await review_evidence.collect_period_tasks(
                session,
                workspace_id=self.workspace_id,
                period_start=period_start,
                period_end=period_end,
                collector=collector,
            )
            context["carried"] = await review_evidence.collect_carry_tasks(
                session,
                workspace_id=self.workspace_id,
                period_start=period_start,
                collector=collector,
            )
            context["journal"] = await review_evidence.collect_journal(
                session,
                workspace_id=self.workspace_id,
                period_key=period_start,
                collector=collector,
            )
            context["learning"] = await review_evidence.collect_learning_changes(
                session,
                workspace_id=self.workspace_id,
                period_key=period_start,
                reviews_root=reviews_root,
                collector=collector,
            )
            context["entries"] = await review_evidence.collect_mistake_entries(
                session,
                workspace_id=self.workspace_id,
                period_key=period_start,
            )
        else:
            source_kind = "daily" if self.kind == "weekly" else "weekly"
            context["entries"] = await review_evidence.collect_entries_for_period(
                session,
                workspace_id=self.workspace_id,
                kind=source_kind,
                period_start=period_start,
                period_end=period_end,
            )
            context["source_sections"] = await review_evidence.collect_sections_for_period(
                session,
                workspace_id=self.workspace_id,
                kind=source_kind,
                period_start=period_start,
                period_end=period_end,
            )
            context["prior"] = await review_evidence.collect_prior_reviews(
                session,
                workspace_id=self.workspace_id,
                kind=self.kind,
                period_key=self.period_key,
                collector=collector,
                section_key="completion_rate" if self.kind == "weekly" else "learning_map",
            )

        snapshot_id = self.payload.get("snapshot_id")
        if collector.items and not snapshot_id:
            # 有证据但无冻结快照：G3 无法复算，标记降级。
            self.degraded_reasons.append("missing")
        if snapshot_id and collector.items:
            await repo.insert_evidence(
                session,
                workspace_id=self.workspace_id,
                run_id=self.run_id,
                snapshot_id=UUID(snapshot_id),
                items=[item.as_dict() for item in collector.items],
            )

        return {
            "evidence": [item.as_dict() for item in collector.items],
            "by_section": collector.by_section,
            "context": {key: value for key, value in context.items() if key != "collector"},
        }

    async def _draft(self, session: AsyncSession) -> None:
        bundle = self.payload.get("bundle") or {}
        collector = _collector_from(bundle.get("evidence") or [])
        context = bundle.get("context") or {}
        period_start, period_end = self._period_bounds()

        if self.kind == "daily":
            ledger = await _ledger(session, self.workspace_id)
            draft = review_composer.compose_daily(
                period_key=period_start,
                collector=collector,
                period_tasks=context.get("period_tasks") or {},
                carried_tasks=context.get("carried") or [],
                journal_docs=context.get("journal") or [],
                learning_docs=context.get("learning") or [],
                entries=self._entries_from_context(context),
            )
            draft.claims = _claims_from_sections(draft)
        elif self.kind == "weekly":
            ledger = await _ledger(session, self.workspace_id)
            recurring = _recurring(context.get("entries") or [])
            draft = review_composer.compose_weekly(
                period_key=self.period_key,
                collector=collector,
                ledger=ledger,
                recurring=recurring,
                method_landing=_method_landing(context.get("entries") or []),
                actions=context.get("entries") or [],
                coverage_gaps=await _coverage_gaps(
                    session, self.workspace_id, self.kind, period_start, period_end
                ),
            )
        else:
            draft = review_composer.compose_yearly(
                period_key=self.period_key,
                collector=collector,
                learning_map=_learning_map(context.get("source_sections") or []),
                timeline=_timeline(context.get("source_sections") or []),
                top_mistakes=_top_mistakes(context.get("entries") or []),
                method_system=_method_system(context.get("entries") or []),
                direction=_direction(context.get("source_sections") or []),
                coverage_gaps=await _coverage_gaps(
                    session, self.workspace_id, self.kind, period_start, period_end
                ),
            )

        errors = review_composer.validate_section_payloads(draft, self.kind)
        if errors:
            logger.warning("draft schema errors: %s", errors[:5])
            self.degraded_reasons.append("missing")

        evidence_by_path = {item.evidence_id: item.as_dict() for item in collector.items}
        source_by_key = {
            (entry["item_type"], entry["canonical_key"]): {
                "source_path": entry.get("source_path"),
                "line_range": entry.get("line_range"),
                "source_date": entry.get("source_date"),
                "evidence_id": entry.get("evidence_id"),
            }
            for entry in context.get("entries") or []
        }
        materialized = materialize_entries(
            draft.as_sections(),
            evidence_by_path=evidence_by_path,
            source_by_key=source_by_key,
        )
        await repo.insert_sections(
            session,
            workspace_id=self.workspace_id,
            review_id=self.review_id,
            version=self.version,
            sections=draft.as_sections(),
        )
        await repo.insert_entries(
            session,
            workspace_id=self.workspace_id,
            review_id=self.review_id,
            version=self.version,
            entries=materialized,
        )
        await repo.insert_citations(
            session,
            workspace_id=self.workspace_id,
            review_id=self.review_id,
            version=self.version,
            citations=draft.citations,
        )
        await session.commit()
        await set_rls_context(session, self.workspace_id, self.payload.get("user_id"))

        self.payload["draft"] = {
            "sections": draft.as_sections(),
            "claims": draft.claims,
            "no_evidence_ratio": draft.no_evidence_ratio,
            "coverage_gaps": draft.coverage_gaps,
            "entries": [
                {
                    "section_key": entry.section_key,
                    "item_type": entry.item_type,
                    "canonical_key": entry.canonical_key,
                    "fields": entry.fields,
                    "source_path": entry.source_path,
                    "line_range": entry.line_range,
                    "evidence_id": entry.evidence_id,
                    "source_date": str(entry.source_date) if entry.source_date else None,
                }
                for entry in materialized
            ],
            "citations": draft.citations,
        }

    def _entries_from_context(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        """daily 的错误/方法论来自当天笔记的抽取结果（Phase 3 为空则走哨兵）。"""
        return context.get("entries") or []

    async def _review(self, session: AsyncSession) -> None:
        bundle = self.payload.get("bundle") or {}
        draft = self.payload.get("draft") or {}
        snapshot_id = self.payload.get("snapshot_id")

        evidence = [EvidenceItem.model_validate(item) for item in bundle.get("evidence") or []]
        sections = draft.get("sections") or []
        entries = draft.get("entries") or []
        claims = draft.get("claims") or []
        coverage_gaps = draft.get("coverage_gaps") or []
        reviews_root = (
            await repo0_settings(session, self.workspace_id) or {}
        ).get("reviews_root") or "wiki/复盘"
        full_path = review_path(reviews_root, self.kind, self.period_key)
        # G9 白名单从 reviews_root 起算（DIRECTORY-CONSTRAINTS §4.1）。
        target_path = full_path[len(reviews_root.rstrip("/")) :].lstrip("/")

        snapshot_hashes = {}
        if snapshot_id:
            snapshot_hashes = await repo.snapshot_hashes(
                session, workspace_id=self.workspace_id, snapshot_id=UUID(snapshot_id)
            )
        frozen_text = await self._frozen_text(bundle, snapshot_hashes)

        ctx = GateContext(
            kind=self.kind,
            workspace_id=str(self.workspace_id),
            sections=sections,
            claims=claims,
            evidence=evidence,
            bundle_snapshot_id=snapshot_id,
            entries=entries,
            snapshot_hashes=snapshot_hashes,
            frozen_text=frozen_text,
            citations=draft.get("citations") or [],
            coverage_gaps=coverage_gaps,
            target_paths=(target_path,),
            parser_version=PARSER_VERSION,
        )
        resolved = {item.evidence_id: str(self.workspace_id) for item in evidence}
        result = run_gates(
            ctx,
            revision_round=int(self.payload.get("revision_round") or 0),
            resolved_owners=resolved,
        )
        verdict = result.verdict
        self.payload["verdict"] = verdict.model_dump()
        self.payload["verdict"]["next_state"] = result.next_state

        if result.next_state == "security_abort":
            await self._transition(
                session,
                "security_abort",
                "tenant_leak",
                payload={**self.payload, "verdict": verdict.model_dump()},
            )
            return
        if result.next_state == "delivering":
            await self._transition(
                session,
                "delivering",
                "gate_pass",
                payload={**self.payload, "verdict": verdict.model_dump()},
            )
            await self._deliver(session)
            return
        if result.next_state == "revising":
            self.payload["revision_round"] = int(self.payload.get("revision_round") or 0) + 1
            await self._transition(
                session,
                "revising",
                "blocking_found",
                payload={**self.payload, "verdict": verdict.model_dump()},
            )
            # Phase 3 的确定性 Writer 无法在轮次内改善内容：直接升级人工，
            # 保证不会无限循环（ARCHITECTURE §4.4 的 ESCALATE）。
            await self._transition(
                session,
                "human_gate",
                "revision_exhausted",
                payload={**self.payload, "verdict": verdict.model_dump()},
            )
            return

        # human_gate：yearly 默认人工，或门禁无法自动收敛
        reason = "yearly_default" if self.kind == "yearly" else "reviewer_disagreement"
        await self._transition(
            session,
            "human_gate",
            reason,
            payload={
                **self.payload,
                "verdict": verdict.model_dump(),
                "reason_code": reason,
            },
        )

    async def _frozen_text(
        self, bundle: dict[str, Any], snapshot_hashes: dict[str, str]
    ) -> dict[str, str]:
        from pathlib import Path

        from mylife_runtime.snapshot_text import load_frozen_text, strip_frontmatter

        storage_path = self.payload.get("storage_path")
        if not storage_path:
            return {}
        texts: dict[str, str] = {}
        for path in snapshot_hashes:
            text = load_frozen_text(Path(storage_path), path)
            if text is None:
                continue
            # `documents.content` 只存正文；证据行号同样以正文第 1 行为基准。
            texts[path] = strip_frontmatter(text)[0]
        return texts

    async def _deliver(self, session: AsyncSession) -> None:
        draft = self.payload.get("draft") or {}
        settings_row = await repo0_settings(session, self.workspace_id)
        reviews_root = (settings_row or {}).get("reviews_root") or "wiki/复盘"
        path = review_path(reviews_root, self.kind, self.period_key)
        generated_at = datetime.now(UTC)

        rendered = render_review_markdown(
            kind=self.kind,
            period_key=self.period_key,
            review_id=str(self.review_id),
            version=self.version,
            snapshot_id=self.payload.get("snapshot_id"),
            generated_at=generated_at,
            sections=[
                {**section, "title": _section_title(self.kind, section["section_key"])}
                for section in draft.get("sections") or []
            ],
        )
        await repo.insert_version(
            session,
            workspace_id=self.workspace_id,
            review_id=self.review_id,
            version=self.version,
            content_md=rendered.content_md,
            content_hash=rendered.content_hash,
            model="deterministic-composer",
            prompt_version=PROMPT_VERSION,
            snapshot_id=UUID(self.payload["snapshot_id"])
            if self.payload.get("snapshot_id")
            else None,
        )
        await repo.finalize_review(
            session,
            workspace_id=self.workspace_id,
            review_id=self.review_id,
            version=self.version,
            content_md=rendered.content_md,
            content_hash=rendered.content_hash,
            model="deterministic-composer",
            git_path=path,
            status="delivered",
            verdict=self.payload.get("verdict"),
            degraded=bool(self.degraded_reasons),
            degraded_reasons=self.degraded_reasons,
        )
        await self._materialize_topics(session, draft)
        await session.commit()
        await set_rls_context(session, self.workspace_id, self.payload.get("user_id"))

        # 交付：Git 连接存在时 commit/PR；否则对象存储包 + ZIP（AC-39）。
        from mylife_api import services_delivery

        review_row = await repo.get_review(
            session, workspace_id=self.workspace_id, review_id=self.review_id
        )
        outcome = await services_delivery.deliver_review(
            session,
            workspace_id=self.workspace_id,
            run_id=self.run_id,
            review_id=self.review_id,
            version=self.version,
            kind=self.kind,
            period_key=self.period_key,
            title=(review_row or {}).get("title") or f"{self.period_key} 复盘",
            content_md=rendered.content_md,
            content_hash=rendered.content_hash,
            degraded_reasons=self.degraded_reasons,
            verdict=self.payload.get("verdict"),
        )
        await session.commit()
        await set_rls_context(session, self.workspace_id, self.payload.get("user_id"))
        reason = {
            "pr_open": "commit_and_pr_ok",
            "merged": "commit_and_pr_ok",
            "noop": "noop",
        }.get(outcome["status"], "local_only")
        payload = {**self.payload, "delivery": {k: str(v) for k, v in outcome.items()}}
        await self._transition(session, "succeeded", reason, payload=payload)

    async def _materialize_topics(self, session: AsyncSession, draft: dict[str, Any]) -> None:
        """AC-68/AC-40：新条目 pending；复发追加 occurrence；字段冲突显式保留。"""
        import json

        topic_by_type = {"error": "errors", "method": "methods", "action": "actions"}
        cleared: set[UUID] = set()
        for entry in draft.get("entries") or []:
            topic = topic_by_type.get(entry["item_type"])
            if topic is None:
                continue
            source_date = entry.get("source_date")
            if isinstance(source_date, str):
                source_date = date.fromisoformat(source_date)
            if source_date is None:
                continue
            fields = entry.get("fields") or {}
            item = await repo.upsert_topic_item(
                session,
                workspace_id=self.workspace_id,
                topic=topic,
                canonical_key=entry["canonical_key"],
                title=_entry_title(entry),
                source_date=source_date,
                fields_json=fields,
            )
            existing_fields = item.get("fields_json") or {}
            if isinstance(existing_fields, str):
                existing_fields = json.loads(existing_fields)
            conflicts = item.get("conflicts_json") or []
            if isinstance(conflicts, str):
                conflicts = json.loads(conflicts)
            changed = False
            for field, value in fields.items():
                old = existing_fields.get(field)
                if old is None or str(old) == str(value):
                    continue
                entry_conflict = {
                    "field": field,
                    "values": [
                        {"date": str(item.get("first_seen") or ""), "value": old},
                        {"date": source_date.isoformat(), "value": value},
                    ],
                }
                conflicts = [
                    conflict
                    for conflict in conflicts
                    if conflict.get("field") != field
                ] + [entry_conflict]
                changed = True
            if changed:
                await repo.update_topic_conflicts(
                    session,
                    workspace_id=self.workspace_id,
                    item_id=item["id"],
                    conflicts=conflicts,
                )
            # 同 period 重跑：每个 item 只清一次旧贡献，再按当前版本重建（D40）。
            if item["id"] not in cleared:
                await repo.delete_topic_occurrences_for_review(
                    session,
                    workspace_id=self.workspace_id,
                    review_id=self.review_id,
                    item_id=item["id"],
                )
                cleared.add(item["id"])
            await repo.insert_topic_occurrence(
                session,
                workspace_id=self.workspace_id,
                item_id=item["id"],
                source_date=source_date,
                source_path=entry.get("source_path") or "",
                line_range=entry.get("line_range"),
                quote=_entry_title(entry),
                evidence_id=entry.get("evidence_id"),
                review_id=self.review_id,
                version=self.version,
                fields_json=fields,
            )
            await repo.refresh_topic_counts(
                session, workspace_id=self.workspace_id, item_id=item["id"]
            )

    # ------------------------------------------------------------------ 失败
    async def _fail(self, session: AsyncSession, code: ErrorCode) -> None:
        try:
            if self.run.get("state") not in TERMINAL_STATES:
                await self._transition(session, "failed", "unhandled")
            await repo.insert_event(
                session,
                workspace_id=self.workspace_id,
                run_id=self.run_id,
                event="error",
                payload={"code": code.value, "state": "failed", "retryable": False},
            )
            await session.commit()
        except Exception:  # pragma: no cover - 最后兜底
            logger.exception("failed to mark review run failed")

    async def _budget_stop(self, session: AsyncSession, exc: BudgetExceeded) -> None:
        try:
            target = "needs_human" if self.run.get("state") == "human_gate" else "failed"
            await self._transition(session, target, "budget_exceeded", payload=self.payload)
            await repo.insert_event(
                session,
                workspace_id=self.workspace_id,
                run_id=self.run_id,
                event="error",
                payload={
                    "code": ErrorCode.BUDGET_EXCEEDED.value,
                    "state": target,
                    "retryable": False,
                    "details": services_budget.budget_exceeded_payload(
                        evaluate(
                            used_input=exc.usage.input_tokens,
                            used_output=exc.usage.output_tokens,
                            used_cost=exc.usage.cost_usd,
                            budget=exc.limit,
                            scope=exc.scope,  # type: ignore[arg-type]
                        )
                    ),
                },
            )
            await session.commit()
        except Exception:  # pragma: no cover
            logger.exception("failed to stop review run on budget")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def repo0_settings(session: AsyncSession, workspace_id: UUID) -> dict[str, Any] | None:
    from mylife_api import repositories as repo0

    return await repo0.get_settings(session, workspace_id)


def _section_title(kind: str, section_key: str) -> str:
    from mylife_contract.constants import SECTION_ORDINALS

    for _ordinal, key, title, _cap in SECTION_ORDINALS[kind]:
        if key == section_key:
            return title
    return section_key


def _collector_from(rows: list[dict[str, Any]]) -> review_evidence.EvidenceCollector:
    collector = review_evidence.EvidenceCollector(
        parser_version=PARSER_VERSION, max_total=MAX_EVIDENCE
    )
    for row in rows:
        collector.add(
            "restored",
            source_type=row["source_type"],
            quote=row["quote"],
            source_path=row.get("source_path"),
            source_date=row.get("source_date"),
            occurred_on=row.get("occurred_on"),
            document_content_hash=row.get("document_content_hash"),
            heading=row.get("heading"),
            line_range=row.get("line_range"),
            document_id=row.get("document_id"),
            chunk_id=row.get("chunk_id"),
            tags=row.get("tags") or [],
        )
    return collector


def _claims_from_sections(draft: review_composer.ComposedDraft) -> list[dict[str, Any]]:
    """把各章节已生成的 claim 汇总（compose_daily 内部已写入 draft.claims）。"""
    return draft.claims


async def _ledger(session: AsyncSession, workspace_id: UUID) -> dict[str, Any]:
    from mylife_api import repo_chat as repo_chat

    return await repo_chat.task_ledger_recalc(session, workspace_id)


async def _coverage_gaps(
    session: AsyncSession,
    workspace_id: UUID,
    kind: str,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    from sqlalchemy import text

    if kind == "weekly":
        result = await session.execute(
            text(
                "SELECT period_key FROM reviews WHERE workspace_id = :ws AND kind = 'daily' "
                "AND status IN ('delivered','approved') "
                "AND period_key BETWEEN :start AND :end"
            ),
            {
                "ws": workspace_id,
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
            },
        )
        present = {row.period_key for row in result}
        gaps = []
        cursor = period_start
        while cursor <= period_end:
            if cursor.isoformat() not in present:
                gaps.append({"date": cursor.isoformat(), "reason": "missing_daily"})
            cursor += timedelta(days=1)
        return gaps

    result = await session.execute(
        text(
            "SELECT period_key FROM reviews WHERE workspace_id = :ws AND kind = 'weekly' "
            "AND status IN ('delivered','approved') AND period_key LIKE :prefix"
        ),
        {"ws": workspace_id, "prefix": f"{period_start.year}-W%"},
    )
    present = {row.period_key for row in result}
    total_weeks = date(period_start.year, 12, 28).isocalendar().week
    return [
        {"period_key": f"{period_start.year}-W{week:02d}", "reason": "missing_weekly"}
        for week in range(1, total_weeks + 1)
        if f"{period_start.year}-W{week:02d}" not in present
    ]


def _recurring(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("item_type") != "error":
            continue
        key = entry["canonical_key"]
        row = grouped.setdefault(
            key,
            {
                "canonical_key": key,
                "occurrences": 0,
                "first_seen": entry.get("source_date"),
                "last_seen": entry.get("source_date"),
            },
        )
        row["occurrences"] += 1
        if entry.get("source_date"):
            if not row["first_seen"] or entry["source_date"] < row["first_seen"]:
                row["first_seen"] = entry["source_date"]
            if not row["last_seen"] or entry["source_date"] > row["last_seen"]:
                row["last_seen"] = entry["source_date"]
    return [
        row
        for row in sorted(
            grouped.values(), key=lambda item: (-item["occurrences"], item["canonical_key"])
        )
        if row["occurrences"] >= 2
    ]


def _method_landing(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [entry for entry in entries if entry.get("item_type") == "method"]
    return [
        {
            "method": (entry.get("fields") or {}).get("name") or entry["canonical_key"][:12],
            "action_count": 0,
            "verification_status": "pending",
        }
        for entry in rows
    ]


def _learning_map(sections: list[dict[str, Any]]) -> dict[str, list[str]]:
    """年报学习地图只读下级 rollup：weekly 的 `method_landing`。

    若存在 daily 形态的 `learning_summary`（用于兼容单元测试），同样并入。
    """
    topics: list[str] = []
    concepts: list[str] = []
    outputs: list[str] = []
    weeks: list[str] = []

    def _push(target: list[str], value: Any) -> None:
        text_value = str(value).strip()
        if text_value and text_value not in target:
            target.append(text_value)

    for section in sections:
        key = section.get("section_key")
        structured = section.get("structured") or {}
        if key == "method_landing":
            for item in structured.get("items", []):
                _push(topics, item.get("method"))
                _push(concepts, item.get("verification_status"))
            period_key = section.get("period_key")
            if isinstance(period_key, str) and period_key.startswith("20"):
                _push(weeks, period_key)
        elif key == "learning_summary":
            _push(topics, structured.get("topic"))
            _push(concepts, structured.get("concept_or_method"))
            _push(outputs, structured.get("output"))
    return {"topics": topics, "concepts": concepts, "outputs": outputs, "related_weeks": weeks}


def _timeline(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """事件线：优先 daily 的 `journal_events`；年报从 weekly 行动项回填。"""
    rows: list[dict[str, Any]] = []
    for section in sections:
        key = section.get("section_key")
        structured = section.get("structured") or {}
        if key == "journal_events":
            for item in structured.get("items", []):
                rows.append(
                    {
                        "date": item.get("date") or section.get("period_key"),
                        "event": item.get("event", ""),
                        "result": item.get("result", ""),
                    }
                )
        elif key == "next_week_actions":
            period_key = section.get("period_key")
            for item in structured.get("items", []):
                if period_key:
                    rows.append(
                        {
                            "date": period_key,
                            "event": item.get("text", ""),
                            "result": item.get("verification", ""),
                        }
                    )
    return [row for row in rows if row["date"]]


def _top_mistakes(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("item_type") != "error":
            continue
        key = entry["canonical_key"]
        row = grouped.setdefault(
            key,
            {
                "canonical_key": key,
                "item": (entry.get("fields") or {}).get("event") or key[:12],
                "occurrences": 0,
                "cost": (entry.get("fields") or {}).get("cost"),
                "source_path": entry.get("source_path"),
            },
        )
        row["occurrences"] += 1
    return sorted(grouped.values(), key=lambda item: (-item["occurrences"], item["canonical_key"]))[
        :10
    ]


def _method_system(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "canonical_key": entry["canonical_key"],
            "name": (entry.get("fields") or {}).get("name"),
            "category": (entry.get("fields") or {}).get("source"),
            "scenario": (entry.get("fields") or {}).get("applicable_scenario"),
            "verification": (entry.get("fields") or {}).get("verification_criteria"),
        }
        for entry in entries
        if entry.get("item_type") == "method"
    ]


def _direction(sections: list[dict[str, Any]]) -> dict[str, str]:
    for section in sections:
        if section.get("section_key") == "next_week_actions":
            items = (section.get("structured") or {}).get("items") or []
            if items:
                first = items[0]
                return {
                    "direction": first.get("text", ""),
                    "basis": first.get("trigger", ""),
                    "first_action": first.get("verification", ""),
                }
    return {}


def _entry_title(entry: dict[str, Any]) -> str:
    fields = entry.get("fields") or {}
    for key in ("name", "event", "text", "root_cause"):
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            return value[:200]
    return entry["canonical_key"][:12]


async def run_review(run_id: UUID, workspace_id: UUID, resume_from: str | None = None) -> None:
    runner = ReviewRunner(run_id, workspace_id)
    await runner.run_from(resume_from)


def review_topic_path(reviews_root: str, topic: str) -> str:
    return topic_path(reviews_root, topic)
