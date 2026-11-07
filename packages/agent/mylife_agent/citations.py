"""引用闭包：只保留能解析到本次 run 冻结证据的 `[n]`，并按首次出现重新编号。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mylife_agent.llm import CHAT_NO_EVIDENCE

_CITATION = re.compile(r"\[(\d{1,3})\]")


@dataclass(frozen=True, slots=True)
class CitationClosure:
    answer: str
    used_indexes: list[int]
    had_invalid: bool


def find_citation_indexes(answer: str) -> list[int]:
    return [int(match.group(1)) for match in _CITATION.finditer(answer or "")]


def close_citations(answer: str, evidence_count: int) -> CitationClosure:
    """把 `[n]` 规范化为 1..k（按首次出现顺序），删除越界标记。

    无证据时统一返回哨兵，保证“不得保留悬空引用”。
    """
    if evidence_count <= 0:
        return CitationClosure(answer=CHAT_NO_EVIDENCE, used_indexes=[], had_invalid=True)

    mapping: dict[int, int] = {}
    order: list[int] = []
    had_invalid = False

    def replace(match: re.Match[str]) -> str:
        nonlocal had_invalid
        original = int(match.group(1))
        if not 1 <= original <= evidence_count:
            had_invalid = True
            return ""
        if original not in mapping:
            order.append(original)
            mapping[original] = len(order)
        return f"[{mapping[original]}]"

    normalized = _CITATION.sub(replace, answer or "").strip()
    if not order:
        return CitationClosure(answer=normalized or answer, used_indexes=[], had_invalid=True)
    return CitationClosure(answer=normalized, used_indexes=order, had_invalid=had_invalid)
