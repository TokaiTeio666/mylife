"""Alembic 环境：迁移使用 mylife_migrator（BYPASSRLS 仅迁移窗口）。"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 允许 `alembic` 在 apps/api 下直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _migration_url() -> str:
    url = os.environ.get("MYLIFE_MIGRATION_URL") or os.environ.get("MYLIFE_DATABASE_URL")
    if not url:
        user = os.environ.get("POSTGRES_USER", "mylife_migrator")
        password = os.environ.get("POSTGRES_PASSWORD", "change-me-migrator")
        host = os.environ.get("MYLIFE_DB_HOST", "localhost")
        port = os.environ.get("MYLIFE_DB_PORT", "5432")
        name = os.environ.get("POSTGRES_DB", "mylife")
        url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"
    return url.replace("postgresql+asyncpg", "postgresql+psycopg")


config.set_main_option("sqlalchemy.url", _migration_url())

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
