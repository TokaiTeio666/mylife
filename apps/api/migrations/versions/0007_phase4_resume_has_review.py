"""Phase 4：续跑扫描函数返回 has_review

Revision ID: 0007_phase4_resume
Revises: 0006_phase4_dispatch
Create Date: 2026-11-08

`dispatch_resumable_review_runs` 是 SECURITY DEFINER；调用方（mylife_app）
再 JOIN `agent_runs` 会重新受 RLS 约束而得到 0 行（无租户上下文）。
把 `has_review` 判断移入函数内部，避免 RLS 逃逸口被 JOIN 破坏。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_phase4_resume"
down_revision: str | None = "0006_phase4_dispatch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 返回列变化必须 DROP + CREATE（PostgreSQL 不允许 OR REPLACE 改返回类型）。
    op.execute("DROP FUNCTION IF EXISTS dispatch_resumable_review_runs(int)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dispatch_resumable_review_runs(p_limit int)
        RETURNS TABLE (
          workspace_id uuid, run_id uuid, state text,
          retry_from_state text, lease_expires_at timestamptz,
          has_review boolean
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT ar.workspace_id, ar.id, ar.state, ar.retry_from_state,
                 ar.lease_expires_at, (ar.state_payload ? 'review_id')
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
    op.execute(
        "REVOKE ALL ON FUNCTION dispatch_resumable_review_runs(int) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION dispatch_resumable_review_runs(int) TO mylife_app"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS dispatch_resumable_review_runs(int)")
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
