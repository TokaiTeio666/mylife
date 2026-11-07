"""mylife 检索原语：jieba 分词、RRF 融合、snippet 高亮。"""

from mylife_retrieval.embedding import (
    DEFAULT_DIMENSION,
    DEFAULT_MODEL,
    EmbeddingProvider,
    EmbeddingUnavailable,
    MockEmbeddingProvider,
    OpenAIEmbeddingProvider,
    build_provider,
    hash_embedding,
)
from mylife_retrieval.rrf import RRFResult, rrf_fuse
from mylife_retrieval.snippet import escape_html, highlight_html, make_snippet
from mylife_retrieval.tokenizer import (
    TOKENIZER_VERSION,
    build_tsquery,
    tokenize,
    tokenize_for_search,
)

__all__ = [
    "DEFAULT_DIMENSION",
    "DEFAULT_MODEL",
    "TOKENIZER_VERSION",
    "EmbeddingProvider",
    "EmbeddingUnavailable",
    "MockEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "RRFResult",
    "build_provider",
    "build_tsquery",
    "escape_html",
    "hash_embedding",
    "highlight_html",
    "make_snippet",
    "rrf_fuse",
    "tokenize",
    "tokenize_for_search",
]
