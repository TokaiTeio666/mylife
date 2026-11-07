"""search 路由（API-CONTRACTS §3.5）。"""

from __future__ import annotations

import time
from datetime import date

from fastapi import APIRouter, Depends, Query
from mylife_contract.errors import ErrorCode
from mylife_contract.search import SearchResponse, SuggestResponse
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import services_search
from mylife_api.deps import RunContext, get_db, get_run_context
from mylife_api.errors import AppError
from mylife_api.ratelimit import get_limiter

router = APIRouter(tags=["search"])

ALLOWED_EXTENSIONS = {"md", "markdown"}


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(min_length=1, max_length=500),
    top_k: int = Query(default=20, ge=1, le=50),
    mode: str = Query(default="hybrid", pattern="^(hybrid|lexical|vector)$"),
    source_type: str = Query(default="both", pattern="^(both|document|review)$"),
    path_prefix: str | None = Query(default=None, max_length=200),
    tags: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    extensions: str | None = Query(default=None),
    include_snippet_html: bool = Query(default=True),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    limit_result = await get_limiter().hit(
        f"rl:search:{ctx.workspace_id}:{ctx.user_id}", 60, 60
    )
    if not limit_result.allowed:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            headers={"Retry-After": str(max(1, limit_result.reset_at - int(time.time())))},
        )

    if path_prefix and ".." in path_prefix.split("/"):
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "path_prefix"})
    tag_list = _split(tags) or None
    if tag_list and len(tag_list) > 10:
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "tags"})
    extension_list = _split(extensions)
    if extension_list:
        extension_list = [item.lower() for item in extension_list]
        if any(item not in ALLOWED_EXTENSIONS for item in extension_list):
            raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "extensions"})
    if date_from and date_to and date_from > date_to:
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "date_from"})

    return await services_search.search(
        db,
        workspace_id=ctx.workspace_id,
        query=q,
        top_k=top_k,
        mode=mode,
        source_type=source_type,
        path_prefix=path_prefix,
        tags=tag_list,
        date_from=date_from,
        date_to=date_to,
        extensions=extension_list,
        include_snippet_html=include_snippet_html,
    )


@router.get("/search/suggest", response_model=SuggestResponse)
async def suggest(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=5, ge=1, le=10),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> SuggestResponse:
    limit_result = await get_limiter().hit(
        f"rl:suggest:{ctx.workspace_id}:{ctx.user_id}", 120, 60
    )
    if not limit_result.allowed:
        raise AppError(ErrorCode.RATE_LIMITED)
    return await services_search.suggest(
        db, workspace_id=ctx.workspace_id, query=q, limit=limit
    )
