"""哈希与归一化（DATA-MODEL §7.3；ARCHITECTURE §9.1 G3d）。"""

from __future__ import annotations

from mylife_runtime.hashing import (
    canonical_key,
    content_hash,
    normalize_text,
    quote_hash,
    root_cause_fingerprint,
)
from mylife_worker.markdown import (
    PARSER_VERSION,
    canonical_frontmatter,
    compute_content_hash,
    normalize_body,
)


def test_normalize_text_rules():
    assert normalize_text("  写　周报  ") == "写 周报"
    assert normalize_text("写周报。") == "写周报."
    assert normalize_text("A，B！C？") == "A,B!C?"
    assert normalize_text("Report") == "Report"  # 默认不改大小写
    assert normalize_text("Report", lowercase=True) == "report"


def test_quote_hash_is_deterministic_and_versioned():
    quote = "缓存穿透是指查询不存在的数据。"
    first = quote_hash(PARSER_VERSION, quote)
    assert first == quote_hash(PARSER_VERSION, quote)
    assert len(first) == 64
    assert first != quote_hash("other-parser", quote)
    # quote_hash 不改英文大小写
    assert quote_hash("p", "Redis Cache") != quote_hash("p", "redis cache")


def test_canonical_key_lowercases_ascii_only():
    assert canonical_key("error", "Report") == canonical_key("error", "report")
    assert canonical_key("error", "报告") == canonical_key("error", "报告")
    assert canonical_key("error", "x") != canonical_key("method", "x")


def test_root_cause_fingerprint():
    assert root_cause_fingerprint("  忘记　备份 ") == root_cause_fingerprint("忘记 备份")
    assert root_cause_fingerprint("A") != root_cause_fingerprint("B")


def test_content_hash_matches_worker_parser():
    body = "# 标题\n\n正文 A\r\n正文 B\n"
    frontmatter = {"tags": ["redis"], "date": "2026-10-27"}
    expected = compute_content_hash(body, frontmatter)
    actual = content_hash(
        PARSER_VERSION, normalize_body(body), canonical_frontmatter(frontmatter)
    )
    assert actual == expected
    assert len(actual) == 64
