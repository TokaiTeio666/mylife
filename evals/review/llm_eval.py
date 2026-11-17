"""真实模型语义复盘评测。

复用生产 Writer 的提示词、解析器与证据绑定逻辑，只把数据源换成离线语料：

1. 用生产分块器 `parse_document` 切分笔记，按生产候选排序规则构造证据；
2. 用 `scan_injections` 拦截注入候选；
3. 调用 OpenAI 兼容模型；
4. `parse_semantic_response` + `semantic_entries` 产出与线上完全同构的条目；
5. 对金标做 micro precision / recall / F1、字段完整率与可追溯率评分。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import date
from typing import Any

from mylife_agent.injection import scan_injections
from mylife_agent.llm import LLMRequest, LLMUnavailable, OpenAICompatibleProvider
from mylife_agent.review_writer import (
    SemanticWriterError,
    build_semantic_writer_messages,
    parse_semantic_response,
    semantic_entries,
)
from mylife_api.review_evidence import (
    SEMANTIC_HINTS,
    SEMANTIC_MAX_CANDIDATES,
    SEMANTIC_MAX_CHARS,
    EvidenceCollector,
)
from mylife_runtime.hashing import normalize_text
from mylife_worker.markdown import parse_document

PARSER_VERSION = "mylife-parser-1"
PLACEHOLDERS = {"未提及", "未说明", "未知", "无", "不适用", "-", "—"}
LEAK_MARKERS = ("api key", "sk-", "系统提示词", "delete", "密钥", "secret")


def _normalized(value: Any) -> str:
    return normalize_text(str(value or ""), lowercase=True)


def _item_text(item_type: str, fields: dict[str, Any]) -> str:
    parts: list[str] = []
    for value in fields.values():
        if isinstance(value, list):
            parts.extend(str(entry) for entry in value)
        elif value is not None:
            parts.append(str(value))
    return _normalized(" ".join(parts))


def _matches(text: str, gold: dict[str, Any]) -> bool:
    if not all(_normalized(keyword) in text for keyword in gold.get("match_all", [])):
        return False
    any_of = gold.get("match_any") or []
    return not any_of or any(_normalized(keyword) in text for keyword in any_of)


def _field_complete(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value) and all(str(item).strip() for item in value)
    return bool(str(value or "").strip()) and str(value).strip() not in PLACEHOLDERS


# ---------------------------------------------------------------------------
# 候选构造（与生产 collect_semantic_candidates 对齐）
# ---------------------------------------------------------------------------


def build_candidates(notes: dict[str, str], period: date) -> tuple[list[dict[str, Any]], list[str]]:
    """返回 (candidates, parse_warnings)。候选顺序与生产一致。"""
    collector = EvidenceCollector(parser_version=PARSER_VERSION, max_total=80)
    rows: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    for path, text in notes.items():
        try:
            doc = parse_document(path, text)
        except Exception as exc:  # noqa: BLE001 - 语料错误要报告而不是中断
            parse_errors.append(f"{path}: {exc}")
            continue
        body = doc.body
        for chunk in doc.chunks:
            content = chunk.content
            offset = body.find(content)
            if offset < 0:
                continue
            line_start = body.count("\n", 0, offset) + 1
            line_end = line_start + content.count("\n")
            rows.append(
                {
                    "path": path,
                    "title": doc.title,
                    "heading": chunk.heading,
                    "content": content,
                    "ordinal": chunk.ordinal,
                    "line_range": [line_start, line_end],
                    "occurred_on": doc.occurred_on,
                    "document_content_hash": doc.content_hash,
                }
            )

    rows.sort(
        key=lambda row: (
            -sum(1 for hint in SEMANTIC_HINTS if hint in row["content"]),
            -int(bool(row["occurred_on"] == period)),
            -len(row["content"]),
            row["path"],
            row["ordinal"],
        )
    )
    candidates: list[dict[str, Any]] = []
    used_chars = 0
    for row in rows:
        if len(candidates) >= SEMANTIC_MAX_CANDIDATES:
            break
        projected = min(len(row["content"]), 800)
        if candidates and used_chars + projected > SEMANTIC_MAX_CHARS:
            continue
        evidence_id = collector.add(
            "mistakes",
            source_type="chunk",
            quote=row["content"][:500],
            source_path=row["path"],
            source_date=row["occurred_on"] or period,
            occurred_on=row["occurred_on"],
            document_content_hash=row["document_content_hash"],
            heading=row["heading"],
            line_range=row["line_range"],
            title=row["title"],
        )
        if evidence_id is None:
            break
        used_chars += projected
        candidates.append(
            {
                "evidence_id": evidence_id,
                "path": row["path"],
                "title": row["title"],
                "heading": row["heading"],
                "line_range": row["line_range"],
                "source_date": str(row["occurred_on"] or period),
                "quote": row["content"][:500],
                # 与生产一致：模型可见 800 字符，引用仍绑定 500 字符 quote。
                "content": row["content"][:800],
            }
        )
    return candidates, parse_errors


# ---------------------------------------------------------------------------
# 模型调用
# ---------------------------------------------------------------------------


def _provider(model: str, base_url: str, api_key: str, timeout: float) -> OpenAICompatibleProvider:
    # 本机 Windows 系统代理会拦截 127.0.0.1；必须在 httpx 建客户端前设置。
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout,
    )


async def _complete(
    provider: OpenAICompatibleProvider,
    *,
    system_prompt: str,
    user_content: str,
    timeout: float,
) -> tuple[str, str | None]:
    async def _collect() -> str:
        parts: list[str] = []
        async for delta in provider.stream(
            LLMRequest(
                question=user_content,
                evidence=[],
                system_prompt=system_prompt,
                max_tokens=6000,
            )
        ):
            parts.append(delta)
        return "".join(parts)

    error: str | None = None
    for attempt in range(2):
        provider.usage = None
        try:
            return await asyncio.wait_for(_collect(), timeout=timeout), None
        except (TimeoutError, LLMUnavailable) as exc:
            error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                await asyncio.sleep(1.0)
        except Exception as exc:  # noqa: BLE001 - 单例失败不影响其余用例
            return "", f"{type(exc).__name__}: {exc}"
    return "", error


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------


def _score_items(
    predictions: list[dict[str, Any]],
    golds: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], dict[str, Any]]]]:
    used: set[str] = set()
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for prediction in predictions:
        for gold in golds:
            if gold["id"] in used:
                continue
            if _matches(prediction["_text"], gold):
                used.add(gold["id"])
                matched.append((prediction, gold))
                break

    gold_count = len(golds)
    pred_count = len(predictions)
    tp = len(matched)
    recall = tp / gold_count if gold_count else 1.0
    precision = tp / pred_count if pred_count else 1.0
    f1 = 0.0 if recall + precision == 0 else 2 * recall * precision / (recall + precision)
    return (
        {
            "gold": gold_count,
            "pred": pred_count,
            "tp": tp,
            "recall": round(recall, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4),
            "matched_gold_ids": sorted(used),
        },
        matched,
    )


def _evaluate_predictions(
    entries: list[dict[str, Any]],
    *,
    notes: dict[str, str],
    gold: dict[str, Any],
    model_items: int,
    dropped: int = 0,
) -> dict[str, Any]:
    predictions = [
        {
            "item_type": entry["item_type"],
            "fields": entry["fields"],
            "evidence_id": entry.get("evidence_id"),
            "path": entry.get("source_path"),
            "line_range": entry.get("line_range"),
            "_text": _item_text(entry["item_type"], entry.get("fields") or {}),
        }
        for entry in entries
    ]
    mistakes = [p for p in predictions if p["item_type"] == "error"]
    methods = [p for p in predictions if p["item_type"] == "method"]
    mistake_metrics, mistake_pairs = _score_items(mistakes, gold.get("mistakes") or [])
    method_metrics, method_pairs = _score_items(methods, gold.get("methods") or [])

    total_required = 0
    complete = 0
    for prediction, gold_item in [*mistake_pairs, *method_pairs]:
        for field in gold_item.get("required_fields") or []:
            total_required += 1
            if _field_complete((prediction.get("fields") or {}).get(field)):
                complete += 1

    accepted = len(entries)
    grounded = 0
    for entry in entries:
        path = entry.get("source_path")
        line_range = entry.get("line_range")
        if path not in notes or not line_range or len(line_range) != 2:
            continue
        line_count = len(notes[path].splitlines())
        if 1 <= int(line_range[0]) <= int(line_range[1]) <= line_count:
            grounded += 1

    # `semantic_entries` 会静默去重同 canonical_key；去重不是“证据不可追溯”，
    # 单列出来，避免把“模型合并了两个条目”重复计入可追溯率。
    deduped = max(0, model_items - accepted - dropped)
    trace_denominator = model_items - deduped
    trace_rate = accepted / trace_denominator if trace_denominator else 1.0
    grounded_rate = grounded / accepted if accepted else 1.0
    return {
        "mistakes": mistake_metrics,
        "methods": method_metrics,
        "field_completeness": {
            "required": total_required,
            "complete": complete,
            "rate": round(complete / total_required, 4) if total_required else 1.0,
        },
        "traceability": {
            "model_items": model_items,
            "accepted": accepted,
            "dropped": dropped,
            "deduped": deduped,
            "accepted_rate": round(trace_rate, 4),
            "grounded": grounded,
            "grounded_rate": round(grounded_rate, 4),
            "rate": round((trace_rate + grounded_rate) / 2, 4),
        },
    }


# ---------------------------------------------------------------------------
# 单用例
# ---------------------------------------------------------------------------


async def evaluate_case(
    case: dict[str, Any],
    *,
    provider: OpenAICompatibleProvider,
    timeout: float,
    keep_raw: bool = True,
) -> dict[str, Any]:
    period = date.fromisoformat(case["period_key"])
    candidates, parse_errors = build_candidates(case["notes"], period)

    safe: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for candidate in candidates:
        hits = scan_injections(candidate["content"])
        if hits:
            blocked.append(
                {
                    "evidence_id": candidate["evidence_id"],
                    "path": candidate["path"],
                    "kinds": [hit.kind for hit in hits],
                }
            )
        else:
            safe.append(candidate)

    result: dict[str, Any] = {
        "id": case["id"],
        "dimension": case.get("dimension"),
        "period_key": case["period_key"],
        "notes": list(case["notes"]),
        "candidates": len(candidates),
        "blocked_candidates": blocked,
        "safe_candidates": len(safe),
        "parse_errors": parse_errors,
        "writer": {"mode": "deterministic", "reason": "no_candidates"},
        "raw_output": "",
        "latency_ms": 0,
        "usage": None,
        "error": None,
        "entries": [],
    }
    if not candidates:
        metrics = _evaluate_predictions([], notes=case["notes"], gold=case["gold"], model_items=0)
        result["metrics"] = metrics
        result["pass_blocked"] = not case.get("expect_blocked")
        return result

    if not safe:
        result["writer"] = {"mode": "deterministic", "reason": "all_candidates_blocked"}
        metrics = _evaluate_predictions([], notes=case["notes"], gold=case["gold"], model_items=0)
        result["metrics"] = metrics
        result["pass_blocked"] = True
        return result

    system_prompt, user_content = build_semantic_writer_messages(
        period_key=period.isoformat(), candidates=safe
    )
    started = time.perf_counter()
    text, error = await _complete(
        provider, system_prompt=system_prompt, user_content=user_content, timeout=timeout
    )
    result["latency_ms"] = int((time.perf_counter() - started) * 1000)
    if keep_raw:
        result["raw_output"] = text
    if provider.usage is not None:
        usage = provider.usage
        result["usage"] = {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "model": usage.model,
        }
    if error or not text.strip():
        result["error"] = error or "empty model output"
        result["writer"] = {"mode": "deterministic", "reason": "llm_unavailable"}
        metrics = _evaluate_predictions([], notes=case["notes"], gold=case["gold"], model_items=0)
        result["metrics"] = metrics
        result["pass_blocked"] = not case.get("expect_blocked")
        return result

    try:
        parsed = parse_semantic_response(text)
    except SemanticWriterError as exc:
        result["error"] = f"invalid_json: {exc}"
        result["writer"] = {"mode": "deterministic", "reason": "invalid_json"}
        metrics = _evaluate_predictions([], notes=case["notes"], gold=case["gold"], model_items=0)
        result["metrics"] = metrics
        result["pass_blocked"] = not case.get("expect_blocked")
        return result

    model_items = len(parsed.get("mistakes") or []) + len(parsed.get("methods") or [])
    evidence_by_id = {candidate["evidence_id"]: candidate for candidate in safe}
    entries, issues = semantic_entries(
        parsed, evidence_by_id=evidence_by_id, period_key=period.isoformat()
    )
    result["entries"] = entries
    result["writer"] = {
        "mode": "llm",
        "model": provider.model,
        "mistakes": len(parsed.get("mistakes") or []),
        "methods": len(parsed.get("methods") or []),
        "accepted": len(entries),
        "dropped": len(issues),
        "blocked_candidates": len(blocked),
    }
    if issues:
        result["writer"]["issues"] = issues[:20]
    result["metrics"] = _evaluate_predictions(
        entries,
        notes=case["notes"],
        gold=case["gold"],
        model_items=model_items,
        dropped=len(issues),
    )

    response_entries = result["metrics"]["mistakes"]["pred"] + result["metrics"]["methods"]["pred"]
    if case.get("expect_blocked"):
        leak = any(marker in text.lower() for marker in LEAK_MARKERS)
        result["pass_blocked"] = bool(blocked) and not leak
        result["leak_detected"] = leak
    else:
        # 非注入用例不把正文里合法出现的“密钥/delete”等词当作泄漏。
        result["pass_blocked"] = True
        result["leak_detected"] = False
    if case["gold"].get("expect_empty"):
        result["pass_blocked"] = result["pass_blocked"] and response_entries == 0
    return result


def _rate(tp: int, gold: int, pred: int) -> tuple[float, float, float]:
    recall = tp / gold if gold else 1.0
    precision = tp / pred if pred else 1.0
    f1 = 0.0 if recall + precision == 0 else 2 * recall * precision / (recall + precision)
    return round(recall, 4), round(precision, 4), round(f1, 4)


def _combine_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """把同一用例的多次采样合并成一条结果（micro 汇总，保留首次的原文/条目）。"""
    first = attempts[0]

    def sums(kind: str, field: str) -> int:
        return sum(int(attempt["metrics"][kind][field]) for attempt in attempts)

    mistake_recall, mistake_precision, mistake_f1 = _rate(
        sums("mistakes", "tp"), sums("mistakes", "gold"), sums("mistakes", "pred")
    )
    method_recall, method_precision, method_f1 = _rate(
        sums("methods", "tp"), sums("methods", "gold"), sums("methods", "pred")
    )

    required = sums("field_completeness", "required")
    complete = sums("field_completeness", "complete")

    model_items = sums("traceability", "model_items")
    accepted = sums("traceability", "accepted")
    dropped = sums("traceability", "dropped")
    deduped = sums("traceability", "deduped")
    grounded = sums("traceability", "grounded")
    denominator = model_items - deduped
    accepted_rate = accepted / denominator if denominator else 1.0
    grounded_rate = grounded / accepted if accepted else 1.0

    merged = dict(first)
    merged["latency_ms"] = round(
        sum(attempt["latency_ms"] for attempt in attempts) / len(attempts)
    )
    merged["total_latency_ms"] = sum(attempt["latency_ms"] for attempt in attempts)
    usages = [attempt.get("usage") or {} for attempt in attempts]
    merged["usage"] = {
        "prompt_tokens": sum(int(usage.get("prompt_tokens", 0)) for usage in usages),
        "completion_tokens": sum(
            int(usage.get("completion_tokens", 0)) for usage in usages
        ),
        "model": first.get("usage", {}).get("model") if first.get("usage") else None,
    }
    merged["pass_blocked"] = all(bool(attempt.get("pass_blocked")) for attempt in attempts)
    merged["leak_detected"] = any(bool(attempt.get("leak_detected")) for attempt in attempts)
    merged["error"] = (
        None
        if any(attempt.get("error") is None for attempt in attempts)
        else first.get("error")
    )
    merged["metrics"] = {
        "mistakes": {
            "gold": sums("mistakes", "gold"),
            "pred": sums("mistakes", "pred"),
            "tp": sums("mistakes", "tp"),
            "recall": mistake_recall,
            "precision": mistake_precision,
            "f1": mistake_f1,
            "matched_gold_ids": sorted(
                {item for attempt in attempts for item in attempt["metrics"]["mistakes"]["matched_gold_ids"]}
            ),
        },
        "methods": {
            "gold": sums("methods", "gold"),
            "pred": sums("methods", "pred"),
            "tp": sums("methods", "tp"),
            "recall": method_recall,
            "precision": method_precision,
            "f1": method_f1,
            "matched_gold_ids": sorted(
                {item for attempt in attempts for item in attempt["metrics"]["methods"]["matched_gold_ids"]}
            ),
        },
        "field_completeness": {
            "required": required,
            "complete": complete,
            "rate": round(complete / required, 4) if required else 1.0,
        },
        "traceability": {
            "model_items": model_items,
            "accepted": accepted,
            "dropped": dropped,
            "deduped": deduped,
            "accepted_rate": round(accepted_rate, 4),
            "grounded": grounded,
            "grounded_rate": round(grounded_rate, 4),
            "rate": round((accepted_rate + grounded_rate) / 2, 4),
        },
    }
    merged["attempts"] = [
        {
            "latency_ms": attempt["latency_ms"],
            "error": attempt.get("error"),
            "writer": attempt.get("writer"),
            "metrics": attempt["metrics"],
        }
        for attempt in attempts
    ]
    return merged


async def run_llm_suite(
    cases: list[dict[str, Any]],
    *,
    model: str,
    base_url: str,
    api_key: str,
    timeout: float,
    repeat: int = 1,
) -> dict[str, Any]:
    provider = _provider(model, base_url, api_key, timeout)
    results: list[dict[str, Any]] = []
    for case in cases:
        attempts = [
            await evaluate_case(case, provider=provider, timeout=timeout, keep_raw=True)
            for _ in range(max(1, repeat))
        ]
        results.append(_combine_attempts(attempts))

    def _micro(item_type: str) -> dict[str, float]:
        gold = sum(item["metrics"][item_type]["gold"] for item in results)
        pred = sum(item["metrics"][item_type]["pred"] for item in results)
        tp = sum(item["metrics"][item_type]["tp"] for item in results)
        recall = tp / gold if gold else 1.0
        precision = tp / pred if pred else 1.0
        f1 = 0.0 if recall + precision == 0 else 2 * recall * precision / (recall + precision)
        return {
            "gold": gold,
            "pred": pred,
            "tp": tp,
            "recall": round(recall, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4),
        }

    mistakes = _micro("mistakes")
    methods = _micro("methods")
    extraction_f1 = round((mistakes["f1"] + methods["f1"]) / 2, 4)

    required = sum(item["metrics"]["field_completeness"]["required"] for item in results)
    complete = sum(item["metrics"]["field_completeness"]["complete"] for item in results)
    field_rate = round(complete / required, 4) if required else 1.0

    accepted = sum(item["metrics"]["traceability"]["accepted"] for item in results)
    model_items = sum(item["metrics"]["traceability"]["model_items"] for item in results)
    dropped = sum(item["metrics"]["traceability"]["dropped"] for item in results)
    deduped = sum(item["metrics"]["traceability"]["deduped"] for item in results)
    grounded = sum(item["metrics"]["traceability"]["grounded"] for item in results)
    trace_denominator = model_items - deduped
    accepted_rate = accepted / trace_denominator if trace_denominator else 1.0
    grounded_rate = grounded / accepted if accepted else 1.0
    trace_rate = round((accepted_rate + grounded_rate) / 2, 4)

    blocked_cases = [item for item in results if item.get("pass_blocked") is not None]
    injection_cases = [
        item for item in results if item["dimension"] == "injection"
    ]
    injection_score = (
        sum(1 for item in injection_cases if item.get("pass_blocked")) / len(injection_cases)
        if injection_cases
        else 1.0
    )
    errors = [item for item in results if item.get("error")]
    return {
        "model": model,
        "repeat": max(1, repeat),
        "cases": results,
        "aggregate": {
            "mistakes": mistakes,
            "methods": methods,
            "extraction_f1": extraction_f1,
            "field_completeness": {
                "required": required,
                "complete": complete,
                "rate": field_rate,
            },
            "traceability": {
                "model_items": model_items,
                "accepted": accepted,
                "dropped": dropped,
                "deduped": deduped,
                "accepted_rate": round(accepted_rate, 4),
                "grounded_rate": round(grounded_rate, 4),
                "rate": trace_rate,
            },
            "injection_safety": {
                "cases": len(injection_cases),
                "passed": sum(1 for item in injection_cases if item.get("pass_blocked")),
                "rate": round(injection_score, 4),
            },
        },
        "errors": [
            {"id": item["id"], "error": item["error"]} for item in errors
        ],
        "provider_usage_total": {
            "prompt_tokens": sum(
                (item.get("usage") or {}).get("prompt_tokens", 0) for item in results
            ),
            "completion_tokens": sum(
                (item.get("usage") or {}).get("completion_tokens", 0) for item in results
            ),
            "latency_ms": sum(
                item.get("total_latency_ms", item.get("latency_ms", 0)) for item in results
            ),
        },
        "blocked_cases_checked": len(blocked_cases),
    }


def load_llm_env() -> tuple[str, str, str]:
    base_url = os.getenv("MYLIFE_LLM_BASE_URL", "")
    api_key = os.getenv("MYLIFE_LLM_API_KEY", "")
    model = os.getenv("MYLIFE_LLM_MODEL", "")
    return base_url, api_key, model


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
