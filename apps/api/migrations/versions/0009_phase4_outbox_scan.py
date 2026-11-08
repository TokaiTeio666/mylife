"""Phase 4：outbox 到期扫描函数

Revision ID: 0009_phase4_outbox_scan
Revises: 0008_phase4_deletion_scan
Create Date: 2026-11-08

`outbox_events` 是 FORCE RLS 租户表；dispatcher 无租户上下文时普通 SELECT
恒为 0 行，outbox 重投永远不会发生（NFR-4 的“DB 提交后崩溃不丢任务”）。
新增 `dispatch_due_outbox` 作为受限 SECURITY DEFINER 逃逸口。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_phase4_outbox_scan"
down_revision: str | None = "0008_phase4_deletion_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dispatch_due_outbox(p_limit int)
        RETURNS TABLE (
          id uuid, workspace_id uuid, topic text, payload_json jsonb, attempts int
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT oe.id, oe.workspace_id, oe.topic, oe.payload_json, oe.attempts
          FROM outbox_events oe
          WHERE oe.status IN ('pending','failed') AND oe.available_at <= now()
          ORDER BY oe.available_at ASC
          LIMIT p_limit
        $$
        """
    )
    op.execute("ALTER FUNCTION dispatch_due_outbox(int) OWNER TO mylife_dispatch")
    op.execute("GRANT SELECT ON outbox_events TO mylife_dispatch")
    op.execute("REVOKE ALL ON FUNCTION dispatch_due_outbox(int) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION dispatch_due_outbox(int) TO mylife_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON FUNCTION dispatch_due_outbox(int) FROM PUBLIC")
    op.execute("DROP FUNCTION IF EXISTS dispatch_due_outbox(int)")
