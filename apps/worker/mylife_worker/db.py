"""Worker 数据库会话与 RLS 上下文。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mylife_worker.config import settings

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.sqlalchemy_url(), pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _factory


async def dispose_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None


@asynccontextmanager
async def job_session(
    workspace_id: UUID, user_id: UUID | None = None
) -> AsyncIterator[AsyncSession]:
    """每个任务一个事务；RLS 绑定到 workspace。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"),
                {"ws": str(workspace_id)},
            )
            await session.execute(
                text("SELECT set_config('app.user_id', :uid, true)"),
                {"uid": str(user_id) if user_id else ""},
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def bind_workspace(session: AsyncSession, workspace_id: Any) -> None:
    await session.execute(
        text("SELECT set_config('app.workspace_id', :ws, true)"),
        {"ws": str(workspace_id)},
    )
