"""jieba 分词（两段式：ingest 侧写 search_text，查询侧用同一词典版本）。"""

from __future__ import annotations

import re
import unicodedata

import jieba

#: 词典版本并入 parser_version，并参与 content_hash（ARCHITECTURE §2.6 / AC-17）。
TOKENIZER_VERSION = f"jieba-{getattr(jieba, '__version__', 'unknown')}:search:v1"

_WS = re.compile(r"\s+")
# 纯标点/符号 token 不进入 tsquery。
_PUNCT_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)
_CJK_RUN = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")


def _ascii_tokens(text: str) -> list[str]:
    """ASCII/数字片段直接按字母数字切分，不交给 jieba。

    对代码块、哈希、日志等长 ASCII 内容，jieba 的 `cut_for_search` 会产生
    O(n²) 级别的开销（实测 40KB 随机十六进制单篇 430ms）；直接切分后
    40KB 文档降到毫秒级，检索语义不受影响。
    """
    return [match.group(0).lower() for match in _ASCII_WORD.finditer(text)]


def tokenize_for_search(text: str) -> list[str]:
    """中文用 `cut_for_search`，非中文按字母数字切分；去重前保序。"""
    if not text:
        return []
    normalized = unicodedata.normalize("NFC", text)
    tokens: list[str] = []
    position = 0
    for match in _CJK_RUN.finditer(normalized):
        if match.start() > position:
            tokens.extend(_ascii_tokens(normalized[position : match.start()]))
        for raw in jieba.cut_for_search(match.group(0)):
            token = raw.strip().lower()
            if not token or _WS.fullmatch(token) or _PUNCT_ONLY.match(token):
                continue
            tokens.append(token)
        position = match.end()
    if position < len(normalized):
        tokens.extend(_ascii_tokens(normalized[position:]))
    return tokens


#: 兼容别名
tokenize = tokenize_for_search


def build_search_text(text: str) -> str:
    """ingest 侧：标题 + 正文 → 空格连接的 search_text。"""
    return " ".join(tokenize_for_search(text))


def _quote(term: str) -> str:
    return "'" + term.replace("'", "''") + "'"


def build_tsquery(text: str) -> tuple[str, str]:
    """返回 (AND 查询, OR 查询)；召回不足时可放宽为 OR。

    术语按出现顺序去重，最多 64 个（SEARCH_TOO_MANY_TERMS 的上限）。
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for token in tokenize_for_search(text):
        if token not in seen:
            seen.add(token)
            tokens.append(token)
        if len(tokens) >= 64:
            break
    if not tokens:
        return "", ""
    terms = [_quote(t) for t in tokens]
    return " & ".join(terms), " | ".join(terms)
