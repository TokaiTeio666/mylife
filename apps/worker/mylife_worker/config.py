"""Worker 配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from mylife_retrieval.embedding import DEFAULT_DIMENSION, DEFAULT_MODEL
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MYLIFE_", env_file=".env", extra="ignore", case_sensitive=False
    )

    env: str = "development"
    database_url: str | None = None
    db_host: str = "localhost"
    db_port: int = 25432
    db_name: str = "mylife"
    app_db_user: str = "mylife_app"
    app_db_password: str = "change-me-app"
    redis_url: str = "redis://localhost:6379/0"

    data_root: Path = Path("./data")
    secret_key: str = "dev-only-change-me-please-32-bytes-min"

    embedding_provider: str = "mock"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str = DEFAULT_MODEL
    embedding_dimension: int = DEFAULT_DIMENSION

    # 仅测试本地裸仓库时开启；生产必须为 false（SEC-12）。
    allow_local_git: bool = False
    # 自托管 Git 的 HTTPS 基址（覆盖 github.com/gitlab.com）；测试用 file:// 裸仓库。
    git_base_url: str | None = None

    parser_version: str = "mylife-parser-1"

    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.app_db_user}:{self.app_db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()


settings = get_settings()
