"""Phase 6：周复盘改为月复盘

Revision ID: 0013_phase6_monthly_review
Revises: 0012_phase5_retire_chunk_table
Create Date: 2026-11-10

- `workspace_settings.weekly_at` → `monthly_at`；
- `workspace_schedules` 新增 monthly，并删除/转换旧的 weekly 配置；
- `reviews` / `agent_runs` / `review_confirmations` 允许新的 `monthly`；
  旧的 `weekly` 行保留为历史数据（应用不再生成或展示 weekly）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_phase6_monthly_review"
down_revision: str | None = "0012_phase5_retire_chunk_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_TABLES_LEGACY = (
    ("reviews", "reviews_kind_check", "('daily','weekly','monthly','yearly')"),
    (
        "agent_runs",
        "agent_runs_kind_check",
        "('chat','daily','weekly','monthly','yearly')",
    ),
    (
        "review_confirmations",
        "review_confirmations_kind_check",
        "('daily','weekly','monthly','yearly')",
    ),
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspace_settings RENAME COLUMN weekly_at TO monthly_at"
    )

    op.execute(
        "ALTER TABLE workspace_schedules DROP CONSTRAINT IF EXISTS "
        "workspace_schedules_kind_check"
    )
    # 旧 weekly 调度先删掉与新 monthly 冲突的行，其余转换为 monthly。
    op.execute(
        "DELETE FROM workspace_schedules w WHERE w.kind = 'weekly' AND EXISTS ("
        "  SELECT 1 FROM workspace_schedules m "
        "  WHERE m.workspace_id = w.workspace_id AND m.kind = 'monthly')"
    )
    op.execute(
        # 清空 next_run_at：由 dispatcher 按 monthly 规则重算下一个月 1 日，
        # 避免迁移瞬间为所有 workspace 补生成上个月月报。
        "UPDATE workspace_schedules SET kind = 'monthly', next_run_at = NULL "
        "WHERE kind = 'weekly'"
    )
    op.execute(
        "ALTER TABLE workspace_schedules ADD CONSTRAINT workspace_schedules_kind_check "
        "CHECK (kind IN ('daily','monthly','yearly'))"
    )

    for table, constraint, kinds in _KIND_TABLES_LEGACY:
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"CHECK (kind IN {kinds})"
        )

    # 活跃 run 唯一索引与续跑扫描函数也必须包含 monthly。
    op.execute("DROP INDEX IF EXISTS uq_runs_review_active")
    op.execute(
        "CREATE UNIQUE INDEX uq_runs_review_active "
        "ON agent_runs (workspace_id, kind, period_key) "
        "WHERE kind IN ('daily','weekly','monthly','yearly') "
        "AND is_active_state(state)"
    )
    op.execute("DROP FUNCTION IF EXISTS dispatch_resumable_review_runs(int)")
    op.execute(
        """
        CREATE FUNCTION dispatch_resumable_review_runs(p_limit int)
        RETURNS TABLE (
          workspace_id uuid, run_id uuid, state text,
          retry_from_state text, lease_expires_at timestamptz,
          has_review boolean
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT ar.workspace_id, ar.id, ar.state, ar.retry_from_state,
                 ar.lease_expires_at, (ar.state_payload ? 'review_id')
          FROM agent_runs ar
          WHERE ar.kind IN ('daily','weekly','monthly','yearly')
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
    op.execute(
        "ALTER TABLE workspace_settings RENAME COLUMN monthly_at TO weekly_at"
    )
    op.execute(
        "ALTER TABLE workspace_schedules DROP CONSTRAINT IF EXISTS "
        "workspace_schedules_kind_check"
    )
    op.execute("DELETE FROM workspace_schedules WHERE kind = 'monthly'")
    op.execute(
        "ALTER TABLE workspace_schedules ADD CONSTRAINT workspace_schedules_kind_check "
        "CHECK (kind IN ('daily','weekly','yearly'))"
    )
    for table, constraint, kinds in _KIND_TABLES_LEGACY:
        legacy_kinds = kinds.replace("'monthly',", "")
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"CHECK (kind IN {legacy_kinds})"
        )
    op.execute("DROP INDEX IF EXISTS uq_runs_review_active")
    op.execute(
        "CREATE UNIQUE INDEX uq_runs_review_active "
        "ON agent_runs (workspace_id, kind, period_key) "
        "WHERE kind IN ('daily','weekly','yearly') AND is_active_state(state)"
    )
    op.execute("DROP FUNCTION IF EXISTS dispatch_resumable_review_runs(int)")
    op.execute(
        """
        CREATE FUNCTION dispatch_resumable_review_runs(p_limit int)
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
