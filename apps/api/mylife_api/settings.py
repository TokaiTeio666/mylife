"""应用配置（环境变量）。

变量命名与 `.env.example` 一致；生产必须显式提供密钥。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MYLIFE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    # 仅在受控调试时开启；开启后未处理异常会记录完整堆栈（可能含 SQL 参数）。
    log_exceptions: bool = False
    public_base_url: str = "http://localhost:8000"
    web_origin: str = "http://localhost:3000"

    # 数据库：优先 MYLIFE_DATABASE_URL；否则按 compose 的部件拼装。
    database_url: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "mylife"
    app_db_user: str = "mylife_app"
    app_db_password: str = "change-me-app"

    redis_url: str = "redis://localhost:6379/0"
    rate_limit_backend: Literal["redis", "memory", "off"] = "redis"
    # 任务队列：redis（Celery）| off（测试/单机调试，任务保持 queued）
    queue_backend: Literal["redis", "off"] = "redis"

    # 数据根（ZIP 原件、快照、导出、交付产物）
    data_root: Path = Path("./data")
    max_zip_bytes: int = 200 * 1024 * 1024

    # 交付：仅测试本地裸仓库时开启（SEC-12）。
    allow_local_git: bool = False
    # 调度器（进程内，单副本）与租约回收。
    dispatcher_enabled: bool = True
    dispatcher_interval_seconds: int = 60
    review_sweeper: bool = True
    review_sweeper_interval_seconds: int = 15
    # 删除冷却天数（SEC-27；测试可调小）。
    deletion_cooldown_days: int = 30

    # 导出（AC-55）：保留期与每日次数上限。
    export_ttl_days: int = 7
    export_daily_limit: int = 3
    # 备份目录（AC-61）；相对路径按仓库根解析。
    backup_dir: Path = Path("./data/backups")

    # 蓝绿换维（AC-50）：覆盖率阈值与旧表保留期。
    embedding_coverage_threshold: float = 0.995
    embedding_retire_days: int = 7
    # 允许用 http:// 访问的私有 provider 主机（逗号分隔，PHASE5-SPEC D26）。
    allowed_private_provider_hosts: str = ""

    # LLM（chat 精简分支）
    llm_provider: str = "mock"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "deepseek-chat"

    # Embedding（查询侧）
    embedding_provider: str = "mock"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimension: int = 1024

    secret_key: str = Field(default="dev-only-change-me-please-32-bytes-min", min_length=32)
    # Git webhook 验签密钥（SEC-16）；未按连接配置时的兜底。
    webhook_secret: str | None = None
    # 安全默认：生产必须为 true；本地 http 调试在 .env 中显式设为 false。
    cookie_secure: bool = True
    session_ttl_days: int = 30
    session_idle_days: int = 7

    # 部署级只读配置（ADMIN_SYSTEM_CONFIG_LOCKED）
    system_config_locked: bool = True

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @field_validator("secret_key")
    @classmethod
    def _secret_not_default_in_prod(cls, v: str) -> str:
        return v

    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.app_db_user}:{self.app_db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def psycopg_dsn(self) -> str:
        """给 Alembic / 健康检查使用的同步 DSN。"""
        url = self.sqlalchemy_url()
        return url.replace("+psycopg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
