"""chunk 表名解析（AC-50 蓝绿换维；PHASE5-SPEC D8）。

蓝绿换维后同一个 workspace 的向量可能存放在 `document_chunks` 或
`document_chunks_v{n}`。所有读写都必须走这里解析出的表名，不能硬编码，
否则切换后查询仍读旧表。

安全：表名来自 `embedding_profiles.chunk_table`（由迁移/`create_chunk_table()`
写入），此处仍做严格校验再拼进 SQL——标识符不能参数化。
"""

from __future__ import annotations

import re

#: 允许的表名形态：基表或 `document_chunks_v<1-4 位数字>`（维度 256..3072）。
CHUNK_TABLE_PATTERN = re.compile(r"^document_chunks(_v[0-9]{1,4})?$")

BASE_CHUNK_TABLE = "document_chunks"


class InvalidChunkTable(ValueError):
    """表名不在允许集合内；拒绝拼进 SQL。"""


def validate_chunk_table(name: str) -> str:
    if not CHUNK_TABLE_PATTERN.match(name or ""):
        raise InvalidChunkTable(f"invalid chunk table: {name!r}")
    return name


def chunk_table_for(dimension: int, *, base_dimension: int = 1024) -> str:
    """按维度推导表名；与基表维度相同则沿用基表。"""
    if dimension == base_dimension:
        return BASE_CHUNK_TABLE
    return f"document_chunks_v{dimension}"
