"""FastAPI 应用入口（API-CONTRACTS §9.2）。"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mylife_contract.constants import API_PREFIX

from mylife_api.errors import install_error_handlers
from mylife_api.logging_setup import configure_logging  # noqa: E402
from mylife_api.middleware import install_middlewares
from mylife_api.ratelimit import get_limiter
from mylife_api.routers import (
    admin,
    auth,
    chat,
    dashboard,
    deletions,
    deliveries,
    documents,
    health,
    ingest,
    reviews,
    search,
    topics,
    webhooks,
    workspaces,
)
from mylife_api.routers import (
    settings as settings_router,
)
from mylife_api.settings import settings

configure_logging(settings.log_level, "mylife-api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    sweeper: asyncio.Task | None = None
    if settings.review_sweeper:
        from mylife_api.services_lease import review_sweeper_loop

        sweeper = asyncio.create_task(
            review_sweeper_loop(settings.review_sweeper_interval_seconds)
        )
    yield
    if sweeper is not None:
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
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
    app.include_router(chat.router, prefix=API_PREFIX)
    app.include_router(reviews.router, prefix=API_PREFIX)
    app.include_router(deliveries.router, prefix=API_PREFIX)
    app.include_router(dashboard.router, prefix=API_PREFIX)
    app.include_router(deletions.router, prefix=API_PREFIX)
    app.include_router(settings_router.router, prefix=API_PREFIX)
    app.include_router(topics.router, prefix=API_PREFIX)
    app.include_router(webhooks.router, prefix=API_PREFIX)
    app.include_router(admin.router, prefix=API_PREFIX)
    return app


app = create_app()
