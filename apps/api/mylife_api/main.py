"""FastAPI 应用入口（API-CONTRACTS §9.2）。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mylife_contract.constants import API_PREFIX

from mylife_api.errors import install_error_handlers
from mylife_api.middleware import install_middlewares
from mylife_api.ratelimit import get_limiter
from mylife_api.routers import (
    admin,
    auth,
    documents,
    health,
    ingest,
    search,
    workspaces,
)
from mylife_api.settings import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format='{"ts":"%(asctime)s","level":"%(levelname)s","service":"%(name)s","event":"%(message)s"}',
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    from mylife_api.db import dispose_engine

    await get_limiter().close()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="mylife API",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    install_error_handlers(app)
    install_middlewares(app)  # type: ignore[arg-type]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Request-Id"],
        expose_headers=["X-Request-Id", "X-API-Version", "X-RateLimit-*", "Retry-After"],
    )

    app.include_router(health.router)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(workspaces.router, prefix=API_PREFIX)
    app.include_router(documents.router, prefix=API_PREFIX)
    app.include_router(ingest.router, prefix=API_PREFIX)
    app.include_router(search.router, prefix=API_PREFIX)
    app.include_router(admin.router, prefix=API_PREFIX)
    return app


app = create_app()
