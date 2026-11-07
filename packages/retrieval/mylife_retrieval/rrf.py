"""确定性 RRF 融合（ARCHITECTURE §2.6，k=60）。

score(d) = Σ_i 1/(60 + rank_i(d))，rank 从 1 起。
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass

RRF_K = 60


@dataclass(slots=True)
class RRFResult:
    key: Hashable
    score: float = 0.0
    rank_lexical: int | None = None
    rank_vector: int | None = None
    rank_title: int | None = None

    @property
    def matched_by(self) -> tuple[str, ...]:
        matched: list[str] = []
        if self.rank_lexical is not None:
            matched.append("lexical")
        if self.rank_vector is not None:
            matched.append("vector")
        if self.rank_title is not None:
            matched.append("title")
        return tuple(matched)


def rrf_fuse(
    routes: Sequence[tuple[str, Iterable[Hashable]]],
    *,
    k: int = RRF_K,
) -> dict[Hashable, RRFResult]:
    """融合多路有序候选。

    `routes` 形如 `[("lexical", [...keys...]), ("vector", [...]), ("title", [...])]`。
    同一 key 在多路出现时分数相加；排序由调用方按 (score desc, path asc, ordinal asc)。
    """
    merged: dict[Hashable, RRFResult] = {}
    for route_name, keys in routes:
        seen_in_route: set[Hashable] = set()
        rank = 0
        for key in keys:
            if key in seen_in_route:
                continue
            seen_in_route.add(key)
            rank += 1
            result = merged.get(key)
            if result is None:
                result = RRFResult(key=key)
                merged[key] = result
            result.score += 1.0 / (k + rank)
            if route_name == "lexical":
                result.rank_lexical = rank
            elif route_name == "vector":
                result.rank_vector = rank
            elif route_name == "title":
                result.rank_title = rank
    return merged


def sort_key(result: RRFResult, path: str, ordinal: int) -> tuple[float, str, int]:
    """排序键：score desc, path asc, chunk_ordinal asc（保证重跑一致）。"""
    return (-result.score, path, ordinal)
