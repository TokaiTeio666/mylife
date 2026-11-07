"""Todo 台账的确定性解析（REQUIREMENTS §6.1；AC-31/32/33）。

纯函数、无 IO、无 LLM：只认 Markdown 复选框，绝不从自然语言推测完成状态。
DB 写入在 `apps/worker/mylife_worker/task_ledger.py`。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from mylife_contract.constants import PUNCT_NORMALIZATION

#: `- [x] text` / `* [ ] text` / `+ [X] text`；缩进不建立父子关系。
CHECKBOX_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<bullet>[-*+])(?P<sep1>[ \t]+)"
    r"\[(?P<mark>[ xX])\](?P<sep2>[ \t]+)(?P<text>\S.*?)[ \t]*$"
)
_FENCE = re.compile(r"^\s*(```|~~~)")
_WS = re.compile(r"\s+", re.UNICODE)
_PUNCT_TABLE = str.maketrans(PUNCT_NORMALIZATION) if PUNCT_NORMALIZATION else {}
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


@dataclass(frozen=True, slots=True)
class TaskOccurrence:
    line_number: int
    checked: bool
    text: str


def normalize_task_text(raw: str) -> str:
    """NFC → 去首尾空白 → 折叠空白 → 中文标点→半角 → ASCII 小写。"""
    value = unicodedata.normalize("NFC", raw or "")
    value = _WS.sub(" ", value).strip()
    value = value.translate(_PUNCT_TABLE).translate(_ASCII_LOWER)
    return value


def task_key(raw: str) -> str:
    """`tasks.key`：归一化原文（不哈希），保证跨天同一任务命中同一行。"""
    return normalize_task_text(raw)


def display_text(raw: str) -> str:
    """展示文本：保留大小写与标点，仅去首尾空白并折叠空白。"""
    return _WS.sub(" ", (raw or "").strip())


def extract_occurrences(
    body_lines: list[str], *, body_start_line: int = 1
) -> list[TaskOccurrence]:
    """从正文行提取复选框任务；fenced code 内的行一律跳过。"""
    occurrences: list[TaskOccurrence] = []
    fence: str | None = None
    for index, line in enumerate(body_lines):
        fence_match = _FENCE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        match = CHECKBOX_RE.match(line)
        if not match:
            continue
        text = display_text(match.group("text"))
        if not text:
            continue
        occurrences.append(
            TaskOccurrence(
                line_number=body_start_line + index,
                checked=match.group("mark") in {"x", "X"},
                text=text,
            )
        )
    return occurrences
