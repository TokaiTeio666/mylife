"""LLM 提供方：mock（确定性、测试/离线）与 OpenAI 兼容流式接口。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import httpx

CHAT_NO_EVIDENCE = "没有找到相关记录"


class LLMUnavailable(RuntimeError):
    """上游模型不可用。"""


class LLMKeyMissing(RuntimeError):
    """未配置模型凭据。"""


@dataclass(slots=True)
class EvidenceQuote:
    evidence_id: str
    path: str
    title: str
    heading: str | None
    quote: str
    line_range: tuple[int, int] | None = None
    document_id: str | None = None
    chunk_id: str | None = None


@dataclass(slots=True)
class LLMRequest:
    question: str
    evidence: Sequence[EvidenceQuote]
    history: Sequence[tuple[str, str]] = field(default_factory=tuple)
    system_prompt: str = ""
    max_tokens: int = 1200


@dataclass(slots=True)
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int
    model: str
    latency_ms: int


class LLMProvider(Protocol):
    model: str
    usage: LLMUsage | None

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]: ...


def _estimate_tokens(text: str) -> int:
    # 中英混排的粗略估算；mock 的计量只需稳定、可复算。
    return max(1, len(text) // 3)


def _first_sentence(text: str, limit: int = 90) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    sentence = re.split(r"(?<=[。！？.!?])\s*", normalized)[0] if normalized else ""
    if len(sentence) > limit:
        sentence = sentence[:limit].rstrip() + "…"
    return sentence


class MockLLMProvider:
    """确定性回答：只依据 evidence 生成带 `[n]` 的句子；无证据返回固定哨兵。

    mock 不解析任何“指令性”文本，因此 prompt injection 不会被执行。
    """

    def __init__(self, model: str = "mock-chat-v1", chunk_size: int = 6) -> None:
        self.model = model
        self.chunk_size = chunk_size
        self.usage: LLMUsage | None = None
        self.last_request: LLMRequest | None = None

    def _compose(self, request: LLMRequest) -> str:
        if not request.evidence:
            return CHAT_NO_EVIDENCE
        sentences: list[str] = []
        for index, quote in enumerate(request.evidence[:6], start=1):
            summary = _first_sentence(quote.quote)
            if not summary:
                continue
            location = f"《{quote.title}》" if quote.title else quote.path
            sentences.append(f"根据{location}，{summary}[{index}]")
        if not sentences:
            return CHAT_NO_EVIDENCE
        sentences.append(f"以上依据来自你知识库中的 {len(sentences)} 条记录。")
        return "；".join(sentences[:-1]) + "。" + sentences[-1]

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        started = time.perf_counter()
        self.last_request = request
        answer = self._compose(request)
        prompt_text = request.system_prompt + request.question + "".join(
            q.quote for q in request.evidence
        )
        self.usage = None
        emitted = 0
        for start in range(0, len(answer), self.chunk_size):
            delta = answer[start : start + self.chunk_size]
            emitted += len(delta)
            yield delta
        latency = int((time.perf_counter() - started) * 1000)
        self.usage = LLMUsage(
            prompt_tokens=_estimate_tokens(prompt_text),
            completion_tokens=_estimate_tokens(answer),
            model=self.model,
            latency_ms=latency,
        )


class OpenAICompatibleProvider:
    """OpenAI/DeepSeek 兼容 `/chat/completions` 流式接口。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key:
            raise LLMKeyMissing("llm api key missing")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.usage: LLMUsage | None = None

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        started = time.perf_counter()
        messages = [{"role": "system", "content": request.system_prompt}]
        messages.extend(
            {"role": role, "content": content} for role, content in request.history
        )
        messages.append({"role": "user", "content": request.question})
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": 0.2,
            "max_tokens": request.max_tokens,
        }
        usage: dict[str, int] = {}
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout_seconds) as client,
                client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                ) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices", []):
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise LLMUnavailable(str(exc)) from exc
        latency = int((time.perf_counter() - started) * 1000)
        self.usage = LLMUsage(
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            model=self.model,
            latency_ms=latency,
        )


def build_llm_provider(
    *,
    provider: str,
    base_url: str | None,
    api_key: str | None,
    model: str,
) -> LLMProvider:
    if provider == "mock":
        return MockLLMProvider()
    if provider in {"openai", "deepseek"}:
        if not base_url or not api_key:
            raise LLMKeyMissing("llm provider not configured")
        return OpenAICompatibleProvider(base_url=base_url, api_key=api_key, model=model)
    raise LLMKeyMissing(f"unsupported llm provider: {provider}")
