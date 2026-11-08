"""active chunk 表名解析（AC-50；PHASE5-SPEC D8）。

放在独立模块里，避免 repo 层反向依赖 service 层。
"""

from __future__ import annotations

from uuid import UUID

from mylife_contract.chunk_tables import BASE_CHUNK_TABLE, validate_chunk_table
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_chunk_table(session: AsyncSession, *, workspace_id: UUID) -> str:
    """当前 workspace 的向量表；无 active profile 时为基表。"""
    row = (
        await session.execute(
            text(
                "SELECT p.chunk_table FROM embedding_profiles p "
                "JOIN workspace_settings s ON s.embedding_profile_id = p.id "
                "WHERE s.workspace_id = :ws"
            ),
            {"ws": workspace_id},
        )
    ).first()
    if row is None or not row.chunk_table:
        return BASE_CHUNK_TABLE
    return validate_chunk_table(str(row.chunk_table))
