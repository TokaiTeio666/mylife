"""测试夹具。

- 数据库测试使用独立库 `mylife_test`，每次会话重建 schema 并跑 Alembic。
- 无 PostgreSQL 时，标记为 `db` 的测试自动跳过，契约测试仍可运行。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# psycopg 的异步模式不支持 Windows 的 ProactorEventLoop。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 必须在导入 mylife_api 之前设置环境变量。
TEST_DB_NAME = os.environ.get("MYLIFE_TEST_DB_NAME", "mylife_test")
TEST_DB_HOST = os.environ.get("MYLIFE_TEST_DB_HOST", "localhost")
TEST_DB_PORT = os.environ.get("MYLIFE_TEST_DB_PORT", "25432")
ADMIN_URL_BASE = os.environ.get(
    "MYLIFE_TEST_ADMIN_URL",
    f"postgresql://mylife_migrator:change-me-migrator@{TEST_DB_HOST}:{TEST_DB_PORT}",
)
APP_PASSWORD = os.environ.get("MYLIFE_APP_DB_PASSWORD", "change-me-app")

os.environ.setdefault("MYLIFE_ENV", "test")
os.environ.setdefault("MYLIFE_RATE_LIMIT_BACKEND", "memory")
os.environ.setdefault("MYLIFE_COOKIE_SECURE", "true")
os.environ.setdefault("MYLIFE_QUEUE_BACKEND", "off")
os.environ.setdefault("MYLIFE_EMBEDDING_PROVIDER", "mock")
os.environ.setdefault("MYLIFE_ALLOW_LOCAL_GIT", "true")
os.environ["MYLIFE_DATABASE_URL"] = (
    f"postgresql+psycopg://mylife_app:{APP_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
)

_TEST_DATA_ROOT = tempfile.mkdtemp(prefix="mylife-test-data-")
os.environ.setdefault("MYLIFE_DATA_ROOT", _TEST_DATA_ROOT)

API_DIR = Path(__file__).resolve().parents[1]


def _admin_connect(dbname: str = "postgres"):
    import psycopg

    return psycopg.connect(f"{ADMIN_URL_BASE}/{dbname}", autocommit=True)


def _pg_available() -> bool:
    try:
        with _admin_connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_AVAILABLE = _pg_available()


def pytest_collection_modifyitems(config, items):
    if PG_AVAILABLE:
        return
    import pytest

    skip = pytest.mark.skip(reason="PostgreSQL 不可用：跳过数据库集成测试")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line("markers", "db: 需要 PostgreSQL 的集成测试")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """限流器是进程级单例；每个用例前清空，避免用例间互相影响。"""
    from mylife_api.ratelimit import get_limiter

    get_limiter()._memory.clear()
    yield
    get_limiter()._memory.clear()


if PG_AVAILABLE:
    import pytest


    @pytest.fixture(scope="session", autouse=True)
    def prepared_database():
        """创建测试库、重建 schema 并迁移到 head。"""
        try:
            with _admin_connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
                ).fetchone()
                if not exists:
                    conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        except Exception:
            pass

        # CI 的 Postgres service 不会执行 infra/postgres/init；这里确保应用角色存在且可登录。
        system_password = os.environ.get("MYLIFE_SYSTEM_DB_PASSWORD", "change-me-system")
        with _admin_connect() as conn:
            for role, password in (
                ("mylife_app", APP_PASSWORD),
                ("mylife_system", system_password),
            ):
                escaped = password.replace("'", "''")
                exists = conn.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
                ).fetchone()
                if exists:
                    conn.execute(
                        f"ALTER ROLE {role} LOGIN NOINHERIT NOBYPASSRLS PASSWORD '{escaped}'"
                    )
                else:
                    conn.execute(
                        f"CREATE ROLE {role} LOGIN NOINHERIT NOBYPASSRLS PASSWORD '{escaped}'"
                    )

        with _admin_connect(TEST_DB_NAME) as conn:
            conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
            conn.execute("CREATE SCHEMA public")
            conn.execute("GRANT USAGE ON SCHEMA public TO mylife_app, mylife_system")
            conn.execute("GRANT ALL ON SCHEMA public TO mylife_migrator")

        env = dict(os.environ)
        env["MYLIFE_MIGRATION_URL"] = f"{ADMIN_URL_BASE}/{TEST_DB_NAME}"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_DIR,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"alembic 迁移失败:\n{result.stdout}\n{result.stderr}")
        yield


    @pytest.fixture(autouse=True)
    def clean_database(request):
        """每个数据库用例前清空所有业务表。"""
        if "db" not in request.node.keywords:
            yield
            return
        with _admin_connect(TEST_DB_NAME) as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                ).fetchall()
            ]
            if tables:
                quoted = ", ".join(f'"{t}"' for t in tables)
                conn.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
        yield


    @pytest.fixture
    async def client():
        import httpx
        from httpx import ASGITransport

        from mylife_api.main import app

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            yield c

    @pytest.fixture
    async def client_factory():
        """按需创建多个独立会话的客户端（用于双设备/跨租户用例）。"""
        import httpx
        from httpx import ASGITransport

        from mylife_api.main import app

        created: list[httpx.AsyncClient] = []

        def make() -> httpx.AsyncClient:
            c = httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="https://testserver"
            )
            created.append(c)
            return c

        yield make
        for c in created:
            await c.aclose()


    @pytest.fixture
    def admin_conn():
        with _admin_connect(TEST_DB_NAME) as conn:
            yield conn


    @pytest.fixture
    async def db_session():
        from mylife_api.db import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            yield session
            # 未显式回滚的会话会以打开事务归还连接池；下一个用例的 TRUNCATE
            # 会与它互相等待（AccessExclusiveLock vs RowShareLock）。
            await session.rollback()
