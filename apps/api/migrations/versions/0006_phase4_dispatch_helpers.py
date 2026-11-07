"""Phase 4 调度辅助函数（租约回收与 NULL next_run_at 补算）

Revision ID: 0006_phase4_dispatch
Revises: 0005_phase4_delivery
Create Date: 2026-11-07

0005 已提交后不再改写；本迁移只做函数的 CREATE OR REPLACE：
- `dispatch_due_schedules` 同时选取 `next_run_at IS NULL` 的启用行，
  让 dispatcher 启动时补算（FX-09）。
- 新增 `dispatch_resumable_review_runs`，供 API 内 sweeper 跨租户扫描
  `created/retry_wait` 与租约过期的复盘 run（PHASE4-SPEC D26）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_phase4_dispatch"
down_revision: str | None = "0005_phase4_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dispatch_due_schedules(p_limit int)
        RETURNS TABLE (
          workspace_id uuid, kind text, local_time time, timezone text,
          next_run_at timestamptz, catchup_days int, enabled boolean
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT ws.workspace_id, ws.kind, ws.local_time, ws.timezone,
                 ws.next_run_at, ws.catchup_days, ws.enabled
          FROM workspace_schedules ws
          WHERE ws.enabled AND (ws.next_run_at IS NULL OR ws.next_run_at <= now())
          ORDER BY ws.next_run_at ASC NULLS FIRST
          LIMIT p_limit
        $$
        """
    )
    op.execute("ALTER FUNCTION dispatch_due_schedules(int) OWNER TO mylife_dispatch")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dispatch_resumable_review_runs(p_limit int)
        RETURNS TABLE (
          workspace_id uuid, run_id uuid, state text,
          retry_from_state text, lease_expires_at timestamptz
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT ar.workspace_id, ar.id, ar.state, ar.retry_from_state, ar.lease_expires_at
          FROM agent_runs ar
          WHERE ar.kind IN ('daily','weekly','yearly')
            AND (
              ar.state IN ('created','retry_wait')
              OR (is_active_state(ar.state)
                  AND (ar.lease_expires_at IS NULL OR ar.lease_expires_at < now()))
            )
          ORDER BY ar.updated_at ASC
          LIMIT p_limit
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION dispatch_resumable_review_runs(int) OWNER TO mylife_dispatch"
    )
    op.execute("GRANT SELECT ON agent_runs TO mylife_dispatch")
    op.execute(
        "REVOKE ALL ON FUNCTION dispatch_resumable_review_runs(int) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION dispatch_resumable_review_runs(int) TO mylife_app"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE ALL ON FUNCTION dispatch_resumable_review_runs(int) FROM PUBLIC"
    )
    op.execute("DROP FUNCTION IF EXISTS dispatch_resumable_review_runs(int)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dispatch_due_schedules(p_limit int)
        RETURNS TABLE (
          workspace_id uuid, kind text, local_time time, timezone text,
          next_run_at timestamptz, catchup_days int, enabled boolean
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT ws.workspace_id, ws.kind, ws.local_time, ws.timezone,
                 ws.next_run_at, ws.catchup_days, ws.enabled
          FROM workspace_schedules ws
          WHERE ws.enabled AND ws.next_run_at IS NOT NULL AND ws.next_run_at <= now()
          ORDER BY ws.next_run_at ASC
          LIMIT p_limit
        $$
        """
    )
