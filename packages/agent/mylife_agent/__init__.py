"""mylife Agent 纯逻辑包：LLM 提供方、提示词、注入检测、引用闭包。"""

from mylife_agent.citations import (
    CitationClosure,
    close_citations,
    find_citation_indexes,
)
from mylife_agent.injection import InjectionHit, scan_injections
from mylife_agent.llm import (
    EvidenceQuote,
    LLMKeyMissing,
    LLMProvider,
    LLMRequest,
    LLMUnavailable,
    LLMUsage,
    MockLLMProvider,
    OpenAICompatibleProvider,
    build_llm_provider,
)
from mylife_agent.prompt import (
    CHAT_NO_EVIDENCE,
    SYSTEM_PROMPT,
    build_evidence_block,
    build_messages,
    plan_tool_calls,
    should_read_note,
)

__all__ = [
    "CHAT_NO_EVIDENCE",
    "SYSTEM_PROMPT",
    "CitationClosure",
    "EvidenceQuote",
    "InjectionHit",
    "LLMKeyMissing",
    "LLMProvider",
    "LLMRequest",
    "LLMUnavailable",
    "LLMUsage",
    "MockLLMProvider",
    "OpenAICompatibleProvider",
    "build_evidence_block",
    "build_llm_provider",
    "build_messages",
    "close_citations",
    "find_citation_indexes",
    "plan_tool_calls",
    "scan_injections",
    "should_read_note",
]
