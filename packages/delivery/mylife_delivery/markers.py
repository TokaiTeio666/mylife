"""`mylife:auto` 区域标记（DIRECTORY-CONSTRAINTS §4.4；PHASE4-SPEC §5.3）。

专题页与索引页由 DB 确定性重渲染，但只允许重写标记内的自动区；
标记外的人工内容必须逐字保留。标记缺失、嵌套或重复时**不得覆盖**，
上层转 `manual` 并记 `GIT_MARKER_BROKEN`。
"""

from __future__ import annotations

import hashlib

from mylife_contract.constants import AUTO_MARKER_END, AUTO_MARKER_START


class MarkerError(Exception):
    """标记不完整/嵌套/重复。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class MarkerBroken(MarkerError):
    """标记缺失或结构非法：转 manual，不覆盖。"""


class ManualDetected(MarkerError):
    """标记内内容与上次自动区哈希不一致：视为人工编辑，不覆盖。"""


def auto_region_hash(inner: str) -> str:
    return hashlib.sha256(inner.encode("utf-8")).hexdigest()


def wrap_auto_region(inner: str) -> str:
    body = inner.strip("\n")
    return f"{AUTO_MARKER_START}\n{body}\n{AUTO_MARKER_END}\n"


def extract_auto_region(text: str) -> tuple[str, str, str]:
    """返回 `(before, inner, after)`。

    要求恰好一对标记，禁止嵌套与重复；否则抛 `MarkerBroken`。
    """
    start_count = text.count(AUTO_MARKER_START)
    end_count = text.count(AUTO_MARKER_END)
    if start_count == 0 and end_count == 0:
        raise MarkerBroken("MISSING")
    if start_count != 1 or end_count != 1:
        raise MarkerBroken("DUPLICATE")
    start = text.index(AUTO_MARKER_START)
    end = text.index(AUTO_MARKER_END)
    if end < start:
        raise MarkerBroken("NESTED")
    inner = text[start + len(AUTO_MARKER_START) : end]
    if AUTO_MARKER_START in inner or AUTO_MARKER_END in inner:
        raise MarkerBroken("NESTED")
    before = text[:start]
    after = text[end + len(AUTO_MARKER_END) :]
    return before, inner.strip("\n"), after


def merge_auto_region(
    existing_text: str | None,
    generated_inner: str,
    *,
    previous_auto_hash: str | None = None,
) -> tuple[str, str]:
    """合并自动区；返回 `(new_text, new_auto_hash)`。

    - 文件不存在：新建“自动区 + 换行”。
    - 标记完整且上次哈希一致（或没有基线）：只替换区内。
    - 标记内哈希与 `previous_auto_hash` 不一致：抛 `ManualDetected`。
    """
    if existing_text is None:
        text = wrap_auto_region(generated_inner)
        return text, auto_region_hash(generated_inner)

    before, inner, after = extract_auto_region(existing_text)
    if previous_auto_hash is not None and auto_region_hash(inner) != previous_auto_hash:
        raise ManualDetected("AUTO_REGION_EDITED")

    merged = f"{before}{wrap_auto_region(generated_inner)}{after.lstrip(chr(10))}"
    if not merged.endswith("\n"):
        merged += "\n"
    return merged, auto_region_hash(generated_inner)


__all__ = [
    "ManualDetected",
    "MarkerBroken",
    "MarkerError",
    "auto_region_hash",
    "extract_auto_region",
    "merge_auto_region",
    "wrap_auto_region",
]
