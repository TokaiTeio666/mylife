"""documents 路由（API-CONTRACTS §3.4）。Phase 0 只实现目录树。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from mylife_contract.documents import DocumentTree, TreeNode
from mylife_contract.errors import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories as repo
from mylife_api.deps import RunContext, get_db, get_run_context
from mylife_api.errors import AppError

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
