"""Embedding 提供方（BGE-M3 / OpenAI 兼容；测试用确定性 mock）。

设计：
- 生产默认 `openai` 兼容接口（SiliconFlow BGE-M3），未配置密钥时不可用；
- `mock` 用 sha256 扩展成固定维度的确定性向量，供 fixtures 与离线测试；
- 维度由配置决定，必须与 `document_chunks.embedding` 列一致（默认 1024）。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

DEFAULT_DIMENSION = 1024
DEFAULT_MODEL = "BAAI/bge-m3"
MAX_BATCH = 64


class EmbeddingUnavailable(RuntimeError):
    """Embedding 不可用：调用方降级为 lexical-only。"""


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimension: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(slots=True)
class MockEmbeddingProvider:
    """确定性向量：同一文本永远得到同一向量；不同文本近似正交。"""

    dimension: int = DEFAULT_DIMENSION
    name: str = "mock"
    model: str = "mock-hash-v1"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [hash_embedding(text, self.dimension) for text in texts]


def hash_embedding(text: str, dimension: int = DEFAULT_DIMENSION) -> list[float]:
    """sha256 计数器模式扩展到任意维度，L2 归一化。"""
    values: list[float] = []
    counter = 0
    while len(values) < dimension:
        digest = hashlib.sha256(f"{counter}\x1f{text}".encode()).digest()
        for i in range(0, len(digest), 4):
            if len(values) >= dimension:
                break
            (chunk,) = int.from_bytes(digest[i : i + 4], "big"),
            values.append((chunk / 0xFFFFFFFF) * 2.0 - 1.0)
        counter += 1
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


@dataclass(slots=True)
class OpenAIEmbeddingProvider:
    base_url: str
    api_key: str
    model: str = DEFAULT_MODEL
    dimension: int = DEFAULT_DIMENSION
    timeout_seconds: float = 10.0
    name: str = "openai"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.api_key:
            raise EmbeddingUnavailable("embedding api key missing")
        results: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for start in range(0, len(texts), MAX_BATCH):
                batch = list(texts[start : start + MAX_BATCH])
                try:
                    response = await client.post(
                        f"{self.base_url.rstrip('/')}/embeddings",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": self.model, "input": batch},
                    )
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise EmbeddingUnavailable(str(exc)) from exc
                payload = response.json()
                vectors = [item["embedding"] for item in payload.get("data", [])]
                if len(vectors) != len(batch):
                    raise EmbeddingUnavailable("embedding count mismatch")
                for vector in vectors:
                    if len(vector) != self.dimension:
                        raise EmbeddingUnavailable(
                            f"embedding dimension {len(vector)} != {self.dimension}"
                        )
                results.extend(vectors)
        return results


def build_provider(
    *,
    provider: str,
    base_url: str | None,
    api_key: str | None,
    model: str,
    dimension: int,
) -> EmbeddingProvider:
    if provider == "mock":
        return MockEmbeddingProvider(dimension=dimension)
    if provider == "openai" and base_url and api_key:
        return OpenAIEmbeddingProvider(
            base_url=base_url, api_key=api_key, model=model, dimension=dimension
        )
    raise EmbeddingUnavailable("embedding provider not configured")
