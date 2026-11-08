"""documents 路由（API-CONTRACTS §3.4）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from mylife_contract.documents import (
    DocumentDetail,
    DocumentMeta,
    DocumentTree,
    TreeNode,
)
from mylife_contract.errors import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories as repo
from mylife_api import services_documents
from mylife_api.deps import RunContext, get_db, get_run_context
from mylife_api.errors import AppError
from mylife_api.rendering import render_markdown

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_NODES = 5000


def _build_tree(rows: list[dict], root: str, depth: int | None) -> tuple[list[TreeNode], bool]:
    """扁平化节点：前端按 parent_path 建树（§3.4.1）。"""
    nodes: dict[str, TreeNode] = {}
    total_documents = len(rows)

    def add_dir(path: str) -> None:
        if path in nodes:
            return
        parent = path.rsplit("/", 1)[0] if "/" in path else None
        nodes[path] = TreeNode(
            type="dir",
            path=path,
            parent_path=parent,
            name=path.rsplit("/", 1)[-1],
            child_count=0,
        )

    for row in rows:
        path: str = row["path"]
        if root and not path.startswith(root.rstrip("/") + "/"):
            continue
        parts = path.split("/")
        if depth is not None and len(parts) > depth:
            continue
        # 目录链
        for i in range(1, len(parts)):
            add_dir("/".join(parts[:i]))
        parent = "/".join(parts[:-1]) if len(parts) > 1 else None
        nodes[path] = TreeNode(
            type="file",
            path=path,
            parent_path=parent,
            name=parts[-1],
            title=row["title"],
            document_id=row["id"],
            size=row["size"],
            mtime=row["mtime"],
            occurred_on=row["occurred_on"],
            tags=list(row["tags"] or []),
            source_type=row["source_type"],
        )

    # 计算目录直接子节点数
    for node in nodes.values():
        if node.type == "dir":
            node.child_count = sum(1 for n in nodes.values() if n.parent_path == node.path)

    ordered = sorted(nodes.values(), key=lambda n: (n.path, n.type != "dir"))
    truncated = len(ordered) > MAX_NODES
    return ordered[:MAX_NODES], truncated or total_documents > MAX_NODES


@router.get("/tree", response_model=DocumentTree)
async def document_tree(
    root: str = Query(default="", max_length=200),
    depth: int | None = Query(default=None, ge=1, le=10),
    include_generated: bool = Query(default=False),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> DocumentTree:
    rows = await repo.list_documents(db, ctx.workspace_id, include_generated=include_generated)
    if root and not any(r["path"].startswith(root.rstrip("/") + "/") for r in rows):
        raise AppError(ErrorCode.NOT_FOUND, details={"root": root})
    nodes, truncated = _build_tree(rows, root, depth)
    return DocumentTree(
        root=root,
        nodes=nodes,
        total_documents=len(rows),
        truncated=truncated,
    )


@router.get("/by-path", response_model=DocumentDetail)
async def get_by_path(
    path: str = Query(min_length=1, max_length=1024),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> DocumentDetail:
    if ".." in path.split("/"):
        raise AppError(ErrorCode.NOT_FOUND)
    return await services_documents.get_by_path(
        db, workspace_id=ctx.workspace_id, path=path
    )


@router.get("/{document_id}/meta", response_model=DocumentMeta)
async def get_meta(
    document_id: UUID,
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> DocumentMeta:
    return await services_documents.get_meta(
        db, workspace_id=ctx.workspace_id, document_id=document_id
    )


@router.get("/{document_id}/content")
async def get_content(
    document_id: UUID,
    format: str = Query(default="markdown", pattern="^(markdown|html)$"),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    detail = await services_documents.get_detail(
        db, workspace_id=ctx.workspace_id, document_id=document_id, include={"content"}
    )
    if format == "html":
        return HTMLResponse(
            render_markdown(detail.content or "", document_id=str(document_id)),
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
                )
            },
        )
    return PlainTextResponse(
        detail.content or "", media_type="text/markdown; charset=utf-8"
    )


def _sanitize_svg(raw: str) -> str:
    """SEC-21：代理 SVG 时移除脚本、foreignObject 与内联事件属性。"""
    import re

    cleaned = re.sub(r"(?is)<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", "", raw)
    cleaned = re.sub(r"(?is)<\s*script\b[^>]*/\s*>", "", cleaned)
    cleaned = re.sub(
        r"(?is)<\s*foreignObject\b[^>]*>.*?<\s*/\s*foreignObject\s*>", "", cleaned
    )
    cleaned = re.sub(r"""(?i)\s+on[a-z0-9_-]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", "", cleaned)
    cleaned = re.sub(r"(?i)javascript\s*:", "blocked:", cleaned)
    return cleaned


@router.get("/{document_id}/asset")
async def get_asset(
    document_id: UUID,
    path: str = Query(min_length=1, max_length=1024),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    target, content_type = await services_documents.get_asset(
        db, workspace_id=ctx.workspace_id, document_id=document_id, path=path
    )
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
    }
    if target.suffix.lower() == ".svg":
        raw = target.read_text(encoding="utf-8", errors="replace")  # noqa: ASYNC240
        return Response(
            content=_sanitize_svg(raw),
            media_type="image/svg+xml; charset=utf-8",
            headers=headers,
        )
    return FileResponse(
        target,
        media_type=content_type,
        headers=headers,
    )


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: UUID,
    include: str = Query(default="meta,content,links"),
    ctx: RunContext = Depends(get_run_context),
    db: AsyncSession = Depends(get_db),
) -> DocumentDetail:
    include_set = {item.strip() for item in include.split(",") if item.strip()}
    if not include_set.issubset({"meta", "content", "links", "chunks"}):
        raise AppError(ErrorCode.VALIDATION_ERROR, details={"field": "include"})
    return await services_documents.get_detail(
        db, workspace_id=ctx.workspace_id, document_id=document_id, include=include_set
    )
