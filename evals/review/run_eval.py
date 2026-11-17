"""复盘能力评测入口。

用法（仓库根目录）：

    # 离线部分（无需数据库 / 模型），适合 CI
    uv run python evals/review/run_eval.py

    # 加上真实模型语义抽取（读取 MYLIFE_LLM_*）
    uv run python evals/review/run_eval.py --llm

输出：
    docs/evidence/review-eval/summary.json   机器可读完整结果
    docs/evidence/review-eval/report.md      人类可读报告
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.review import corpus, llm_eval, offline

WEIGHTS: dict[str, float] = {
    "semantic_extraction": 0.25,
    "field_completeness": 0.05,
    "traceability": 0.05,
    "injection_safety": 0.10,
    "gate_enforcement": 0.15,
    "todo_ledger": 0.10,
    "action_generation": 0.05,
    "assembly": 0.05,
    "rollup": 0.10,
    "date_resolution": 0.05,
    "determinism": 0.05,
}

DIMENSION_LABELS = {
    "semantic_extraction": "语义抽取（错误/方法论 F1）",
    "field_completeness": "金标字段完整率",
    "traceability": "证据可追溯率",
    "injection_safety": "提示注入安全",
    "gate_enforcement": "G0–G9 门禁",
    "todo_ledger": "Todo 台账文法",
    "action_generation": "行动清单生成（FR-E6/E7）",
    "assembly": "章节装配与哨兵",
    "rollup": "月/年 rollup",
    "date_resolution": "业务日期解析",
    "determinism": "确定性重跑",
}


def _weighted(dimensions: dict[str, float], *, llm: bool) -> tuple[float, dict[str, float]]:
    weights = dict(WEIGHTS)
    if not llm:
        for key in ("semantic_extraction", "field_completeness", "traceability"):
            weights.pop(key, None)
    total = sum(weights.values())
    normalized = {key: value / total for key, value in weights.items()}
    overall = sum(dimensions[key] * normalized[key] for key in normalized)
    return round(overall * 100, 2), {key: round(value, 4) for key, value in normalized.items()}


def _suite_rows(suites: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key, suite in suites.items():
        passed = sum(1 for item in suite["checks"] if item["passed"])
        rows.append(
            {
                "dimension": key,
                "label": DIMENSION_LABELS.get(key, key),
                "passed": passed,
                "total": len(suite["checks"]),
                "score": round(suite["score"], 4),
                "failed": [item["name"] for item in suite["checks"] if not item["passed"]],
            }
        )
    return rows


def _write_report(
    *,
    out_dir: Path,
    summary: dict[str, Any],
    suites: dict[str, Any],
    llm_result: dict[str, Any] | None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    lines: list[str] = []
    lines.append("# 复盘能力评测报告")
    lines.append("")
    lines.append(f"- 生成时间：`{summary['generated_at']}`")
    lines.append(f"- 语料版本：`{summary['corpus_version']}`")
    if summary["llm"]:
        lines.append(
            f"- 真实模型：`{summary['llm']['model']}`（每用例采样 {summary['llm']['repeat']} 次）"
        )
    else:
        lines.append("- 真实模型：未启用")
    lines.append(f"- 总体得分：**{summary['overall']} / 100**")
    lines.append("")
    lines.append("## 评分维度")
    lines.append("")
    lines.append("| 维度 | 权重 | 得分 | 通过/总数 | 失败项 |")
    lines.append("|---|---:|---:|---:|---|")
    for key, weight in summary["weights"].items():
        lookup = "injection_scanner" if key == "injection_safety" else key
        row = next(
            (item for item in summary["offline"]["dimensions"] if item["dimension"] == lookup),
            None,
        )
        if row is not None:
            score = row["score"]
            counts = f"{row['passed']}/{row['total']}"
            failed = "、".join(row["failed"][:6]) or "—"
        else:
            aggregate = summary["llm_aggregate"] or {}
            score = aggregate.get(
                "extraction_f1" if key == "semantic_extraction" else key, 0.0
            )
            counts = "—"
            failed = "—"
        lines.append(
            f"| {DIMENSION_LABELS.get(key, key)} | {weight:.0%} | {score:.2%} | {counts} | {failed} |"
        )
    lines.append("")

    if llm_result:
        aggregate = llm_result["aggregate"]
        lines.append("## 语义抽取明细")
        lines.append("")
        lines.append(
            f"- 错误：gold={aggregate['mistakes']['gold']} pred={aggregate['mistakes']['pred']} "
            f"TP={aggregate['mistakes']['tp']} "
            f"P={aggregate['mistakes']['precision']:.2%} R={aggregate['mistakes']['recall']:.2%} "
            f"F1={aggregate['mistakes']['f1']:.2%}"
        )
        lines.append(
            f"- 方法论：gold={aggregate['methods']['gold']} pred={aggregate['methods']['pred']} "
            f"TP={aggregate['methods']['tp']} "
            f"P={aggregate['methods']['precision']:.2%} R={aggregate['methods']['recall']:.2%} "
            f"F1={aggregate['methods']['f1']:.2%}"
        )
        lines.append(f"- 抽取 F1（两类平均）：{aggregate['extraction_f1']:.2%}")
        lines.append(
            f"- 字段完整率：{aggregate['field_completeness']['rate']:.2%} "
            f"（{aggregate['field_completeness']['complete']}/{aggregate['field_completeness']['required']}）"
        )
        trace = aggregate["traceability"]
        lines.append(
            f"- 可追溯率：{trace['rate']:.2%} "
            f"（接受 {trace['accepted']}/{trace['model_items']}，"
            f"丢弃 {trace['dropped']}，去重 {trace['deduped']}，"
            f"落点 {trace['grounded_rate']:.2%}）"
        )
        lines.append(
            f"- 注入安全：{aggregate['injection_safety']['passed']}/{aggregate['injection_safety']['cases']}"
        )
        lines.append("")
        lines.append("| 用例 | 维度 | 错误 P/R | 方法 P/R | 字段 | 可追溯 | 拦截 | 延迟 ms |")
        lines.append("|---|---|---|---|---:|---:|---|---:|")
        for case in llm_result["cases"]:
            m = case["metrics"]["mistakes"]
            p = case["metrics"]["methods"]
            blocked = "是" if case.get("blocked_candidates") else "—"
            lines.append(
                f"| {case['id']} | {case['dimension']} | "
                f"{m['precision']:.0%}/{m['recall']:.0%} | "
                f"{p['precision']:.0%}/{p['recall']:.0%} | "
                f"{case['metrics']['field_completeness']['rate']:.0%} | "
                f"{case['metrics']['traceability']['rate']:.0%} | "
                f"{blocked} | {case['latency_ms']} |"
            )
        lines.append("")
        if llm_result.get("errors"):
            lines.append("### 模型调用失败")
            lines.append("")
            for item in llm_result["errors"]:
                lines.append(f"- `{item['id']}`：{item['error']}")
            lines.append("")

    lines.append("## 离线门禁与结构检查")
    lines.append("")
    for key, suite in suites.items():
        failed = [item for item in suite["checks"] if not item["passed"]]
        lines.append(f"### {DIMENSION_LABELS.get(key, key)}：{suite['score']:.2%}")
        if not failed:
            lines.append("")
            lines.append("全部通过。")
        else:
            lines.append("")
            lines.append("| 检查项 | 详情 |")
            lines.append("|---|---|")
            for item in failed:
                detail = str(item["detail"]).replace("|", "\\|")[:300]
                lines.append(f"| {item['name']} | {detail} |")
        lines.append("")

    lines.append("## 方法论")
    lines.append("")
    lines.append("- 语义抽取直接复用生产 `review_writer` 的提示词、JSON 解析与证据绑定；")
    lines.append("- 门禁、装配、Todo、日期、rollup、渲染全部调用生产代码，不另写判定逻辑；")
    lines.append("- 金标按“关键词集合”匹配，避免逐字比对造成的假阴性；")
    lines.append("- 分数只用于回归比较，不替代 `docs/evidence/phase3/sampling.md` 的人工事实复核。")
    lines.append("")
    lines.append("## 已知口径")
    lines.append("")
    lines.append("- 语义用例的 `S11_long_note_tail` 故意把错误放在候选 800 字符截断之后，")
    lines.append("  用于暴露“长段落证据截断导致漏检”的现有边界；")
    lines.append("- `S09_multi_items` 用于暴露“共享根因的不同事故被合并成一条”的提示词边界；")
    lines.append("- `action_generation` 的失败项是产品缺口（错误/方法论未转为行动条目），")
    lines.append("  不是评测脚本缺陷；显式 action 条目的正向对照会正常通过；")
    lines.append("- 单次运行受模型采样影响；基准建议使用 `--repeat 3`，分数用于回归比较而非绝对排名；")
    lines.append("- 未启用 `--llm` 时，语义/字段/可追溯三项不参与总分，权重按剩余维度归一化。")
    lines.append("")

    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mylife 复盘能力评测")
    parser.add_argument("--llm", action="store_true", help="启用真实模型语义抽取评测")
    parser.add_argument("--model", default=None, help="覆盖 MYLIFE_LLM_MODEL")
    parser.add_argument("--base-url", default=None, help="覆盖 MYLIFE_LLM_BASE_URL")
    parser.add_argument("--api-key", default=None, help="覆盖 MYLIFE_LLM_API_KEY")
    parser.add_argument("--timeout", type=float, default=180.0, help="单次模型调用超时秒数")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="每个语义用例采样次数；>1 时取平均，用来看模型方差",
    )
    parser.add_argument("--case", action="append", default=None, help="只跑指定语义用例 ID")
    parser.add_argument(
        "--out",
        default=str(ROOT / "docs" / "evidence" / "review-eval"),
        help="证据输出目录",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=0.0,
        help="总分低于该值时以非零码退出（CI 门禁用）",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="不重新评测，只根据现有 summary.json 重写 report.md",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    if args.render_only:
        summary_path = out_dir / "summary.json"
        if not summary_path.exists():
            print(f"missing {summary_path}", file=sys.stderr)
            return 2
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        _write_report(
            out_dir=out_dir,
            summary=summary,
            suites=summary["offline"]["suites"],
            llm_result=summary["semantic"],
        )
        print(f"re-rendered {out_dir / 'report.md'}")
        return 0

    suites = offline.run_offline_suites()
    dimensions: dict[str, float] = {
        key: suite["score"] for key, suite in suites.items() if key != "injection_scanner"
    }
    dimensions["injection_safety"] = suites["injection_scanner"]["score"]

    llm_result: dict[str, Any] | None = None
    if args.llm:
        base_url, api_key, model = llm_eval.load_llm_env()
        base_url = args.base_url or base_url
        api_key = args.api_key or api_key
        model = args.model or model
        if not (base_url and api_key and model):
            print("MYLIFE_LLM_BASE_URL / MYLIFE_LLM_API_KEY / MYLIFE_LLM_MODEL 未配置", file=sys.stderr)
            return 2
        cases = corpus.SEMANTIC_CASES
        if args.case:
            wanted = set(args.case)
            cases = [case for case in cases if case["id"] in wanted]
        llm_result = asyncio.run(
            llm_eval.run_llm_suite(
                cases,
                model=model,
                base_url=base_url,
                api_key=api_key,
                timeout=args.timeout,
                repeat=args.repeat,
            )
        )
        aggregate = llm_result["aggregate"]
        dimensions["semantic_extraction"] = aggregate["extraction_f1"]
        dimensions["field_completeness"] = aggregate["field_completeness"]["rate"]
        dimensions["traceability"] = aggregate["traceability"]["rate"]
        dimensions["injection_safety"] = min(
            dimensions["injection_safety"], aggregate["injection_safety"]["rate"]
        )

    overall, weights = _weighted(dimensions, llm=llm_result is not None)

    summary = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "corpus_version": "review-eval-v1",
        "llm": (
            {
                "model": llm_result["model"],
                "repeat": llm_result["repeat"],
                "provider_usage_total": llm_result["provider_usage_total"],
            }
            if llm_result
            else None
        ),
        "overall": overall,
        "weights": weights,
        "dimensions": {key: round(value, 4) for key, value in dimensions.items()},
        "llm_aggregate": (
            {
                "extraction_f1": llm_result["aggregate"]["extraction_f1"],
                "field_completeness": llm_result["aggregate"]["field_completeness"]["rate"],
                "traceability": llm_result["aggregate"]["traceability"]["rate"],
                "injection_safety": llm_result["aggregate"]["injection_safety"]["rate"],
            }
            if llm_result
            else None
        ),
        "offline": {"dimensions": _suite_rows(suites), "suites": suites},
        "semantic": llm_result,
    }

    _write_report(out_dir=out_dir, summary=summary, suites=suites, llm_result=llm_result)

    print(f"overall={overall}/100  llm={'on' if llm_result else 'off'}")
    for key, value in dimensions.items():
        print(f"  {key:<22} {value:.2%}")
    print(f"wrote {out_dir / 'summary.json'}")
    print(f"wrote {out_dir / 'report.md'}")
    if overall < args.fail_under:
        print(f"FAIL: overall {overall} < --fail-under {args.fail_under}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
