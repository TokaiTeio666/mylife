"""提示注入检测（SEC-10 / FX-05）。

检测到命中时：该证据不进入 LLM 上下文，写 `security.prompt_injection` 事件，
并继续以其余证据回答。检测不静默过滤：事件与审计保留。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InjectionPattern:
    kind: str
    pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class InjectionHit:
    kind: str
    matched: str


PATTERNS: tuple[InjectionPattern, ...] = (
    InjectionPattern(
        "instruction_override",
        re.compile(
            r"(忽略|无视|忘记|不要理会).{0,12}(以上|之前|前面|所有).{0,8}(指令|要求|规则|提示)"
            r"|ignore\s+(all\s+)?(previous|above)\s+instructions",
            re.IGNORECASE,
        ),
    ),
    InjectionPattern(
        "external_image",
        re.compile(r"!\[[^\]]*\]\(\s*https?://|<img[^>]+src\s*=\s*[\"']https?://", re.IGNORECASE),
    ),
    InjectionPattern(
        "html_script",
        re.compile(r"<\s*script\b|on(error|load|click)\s*=|javascript:", re.IGNORECASE),
    ),
    InjectionPattern(
        "path_traversal",
        re.compile(r"(\.\./){2,}|/etc/passwd|file://|C:\\\\Windows", re.IGNORECASE),
    ),
    InjectionPattern(
        "fake_evidence_delimiter",
        re.compile(r"<<<\s*(EVIDENCE|END)|<\s*<\s*<\s*EVIDENCE", re.IGNORECASE),
    ),
    InjectionPattern(
        "secret_exfiltration",
        re.compile(
            r"(输出|打印|显示|返回|告诉我).{0,10}(API\s*Key|密钥|token|密码|secret)"
            r"|\bsk-[A-Za-z0-9]{8,}",
            re.IGNORECASE,
        ),
    ),
    InjectionPattern(
        "write_tool_request",
        re.compile(
            r"(调用|执行|使用).{0,10}(create_review|delete|删除|写入|写工具|write\s+tool)",
            re.IGNORECASE,
        ),
    ),
    InjectionPattern(
        "cross_workspace_read",
        re.compile(
            r"workspace_id|跨租户|其他\s*(用户|租户|工作区)|other\s+workspace",
            re.IGNORECASE,
        ),
    ),
)


def scan_injections(text: str) -> list[InjectionHit]:
    hits: list[InjectionHit] = []
    for item in PATTERNS:
        match = item.pattern.search(text or "")
        if match:
            hits.append(InjectionHit(kind=item.kind, matched=match.group(0)[:120]))
    return hits
