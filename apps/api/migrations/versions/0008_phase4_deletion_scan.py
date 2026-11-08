"""Phase 4：删除冷却到期扫描函数

Revision ID: 0008_phase4_deletion_scan
Revises: 0007_phase4_resume
Create Date: 2026-11-08

`deletion_requests` 是 FORCE RLS 租户表；dispatcher 没有租户上下文，
直接 `SELECT ... WHERE state='cooling'` 恒为 0 行，SEC-27 的 purge 会静默
不执行。新增 `dispatch_due_deletions` 作为受限 SECURITY DEFINER 逃逸口，
只返回到期记录的三列。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_phase4_deletion_scan"
down_revision: str | None = "0007_phase4_resume"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dispatch_due_deletions(p_limit int)
        RETURNS TABLE (id uuid, workspace_id uuid, scope text)
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT dr.id, dr.workspace_id, dr.scope
          FROM deletion_requests dr
          WHERE dr.state = 'cooling' AND dr.purge_at <= now()
          ORDER BY dr.purge_at ASC
          LIMIT p_limit
        $$
        """
    )
    op.execute("ALTER FUNCTION dispatch_due_deletions(int) OWNER TO mylife_dispatch")
    op.execute("GRANT SELECT ON deletion_requests TO mylife_dispatch")
    op.execute("REVOKE ALL ON FUNCTION dispatch_due_deletions(int) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION dispatch_due_deletions(int) TO mylife_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON FUNCTION dispatch_due_deletions(int) FROM PUBLIC")
    op.execute("DROP FUNCTION IF EXISTS dispatch_due_deletions(int)")
