"""数据库会话与 RLS 注入。

每个请求一个事务；`set_config('app.workspace_id', ..., true)` 为事务局部设置，
无值时 `app_workspace_id()` 返回 NULL，RLS 默认拒绝（DATA-MODEL §6.3）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mylife_api.settings import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.sqlalchemy_url(),
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """独立事务（用于后台/脚本）。"""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        yield session


async def set_rls_context(
    session: AsyncSession, workspace_id: Any | None, user_id: Any | None
) -> None:
    """设置 RLS 上下文；必须在事务内调用。"""
    await session.execute(
        text("SELECT set_config('app.workspace_id', :ws, true)"),
        {"ws": str(workspace_id) if workspace_id else ""},
    )
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, true)"),
        {"uid": str(user_id) if user_id else ""},
    )


async def clear_rls_context(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
    await session.execute(text("SELECT set_config('app.user_id', '', true)"))
