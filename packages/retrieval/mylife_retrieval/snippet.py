"""snippet 与高亮。

规则（ARCHITECTURE §2.6 / AC-18）：**先 HTML 转义，再插入 `<mark>`**，防止 XSS。
"""

from __future__ import annotations

import html
import re

_WS = re.compile(r"\s+")
_TOKEN_SPLIT = re.compile(r"[\s，。、；：！？,.;:!?()（）\[\]【】]+")


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def _terms(query: str | list[str]) -> list[str]:
    if isinstance(query, str):
        from mylife_retrieval.tokenizer import tokenize_for_search

        terms = tokenize_for_search(query)
    else:
        terms = [t.strip().lower() for t in query if t and t.strip()]
    # 长词优先，减少短词破坏长词高亮
    return sorted(set(terms), key=lambda t: (-len(t), t))


def highlight_html(text: str, query: str | list[str]) -> str:
    """转义文本并在命中词外插入 `<mark>`。"""
    escaped = escape_html(text)
    for term in _terms(query):
        escaped_term = escape_html(term)
        if not escaped_term:
            continue
        escaped = re.sub(
            re.escape(escaped_term),
            lambda m: f"<mark>{m.group(0)}</mark>",
            escaped,
            flags=re.IGNORECASE,
        )
    # 避免重复嵌套（例如短词落在已标记长词内）
    return escaped.replace("<mark><mark>", "<mark>").replace("</mark></mark>", "</mark>")


def _count_words(text: str) -> int:
    return len([part for part in _TOKEN_SPLIT.split(text) if part])


def make_snippet(
    text: str,
    query: str | list[str],
    *,
    min_words: int = 15,
    max_words: int = 40,
) -> tuple[str, str]:
    """返回 (纯文本 snippet, 已转义并高亮的 snippet_html)。

    以最早命中词为中心取窗口；窗口目标 `max_words` 个片段，最少 `min_words`。
    """
    normalized = _WS.sub(" ", text or "").strip()
    if not normalized:
        return "", ""

    terms = _terms(query)
    lowered = normalized.lower()
    positions = [lowered.find(term) for term in terms]
    positions = [p for p in positions if p >= 0]
    hit = min(positions) if positions else 0

    # 以命中点为中心，先取字符窗口，再按片段边界扩展/收缩。
    span = max(120, max_words * 12)
    start = max(0, hit - span // 3)
    end = min(len(normalized), start + span)
    if end - start < span and start > 0:
        start = max(0, end - span)

    window = normalized[start:end]
    if _count_words(window) > max_words:
        # 保留命中所在的一侧，截断到 max_words
        window = " ".join(window.split(" ")[:max_words])
    elif _count_words(window) < min_words and len(normalized) > len(window):
        window = normalized[start : min(len(normalized), start + min_words * 12)]

    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(normalized) else ""
    snippet = f"{prefix}{window.strip()}{suffix}"
    return snippet, highlight_html(snippet, terms)


def snippet_from_heading(text: str, heading: str | None, query: str | list[str]) -> tuple[str, str]:
    source = f"{heading}\n{text}" if heading else text
    return make_snippet(source, query)
