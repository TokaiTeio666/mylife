"""Phase 4：交付、专题文件、调度、仪表盘与删除冷却的数据模型

Revision ID: 0005_phase4_delivery
Revises: 0004_phase3
Create Date: 2026-11-07

对齐：ARCHITECTURE §8/§11/§16.6、DATA-MODEL §4.14–§4.20/§9、API-CONTRACTS
§3.7.10–§3.9、AC-39/40/43/51/52/67、SECURITY-BOUNDARY SEC-27。

与文档的偏差（登记 docs/IMPLEMENTATION-NOTES.md §2D）：
- agent_runs.trigger_type：调度来源需要可查询的落点（PHASE4-SPEC D25）。
- git_connections.remote_url/api_base_url/credential_status/last_tested_at/
  last_test_error：自托管/测试远端与连接测试矩阵（D21–D23）。
- delivery_records.manifest_json/diff_summary_json/error_code/auto_merge/
  attempts/content_hash/base_branch/merged_at：GET /deliveries/{id} 与重试幂等
  （D36/D41）。
- topic_items.fields_json/conflicts_json/dropped_at；topic_occurrences 增
  review_id/version/review_entry_id/fields_json，line_start 回填 0 并 NOT NULL
  （D3/D5/D7/D8/D40）。
- topic_files：专题 md 的 auto 区哈希与 manual 状态（D6）。
- outbox_events / dispatcher_heartbeats：ARCHITECTURE §11/§16.6 的 outbox 与
  心跳（D27）。
- deletion_requests / export_jobs：SEC-27 最小删除链路（D17/D18/D19）。
- dispatch_due_schedules()/dispatch_due_git_syncs()：跨租户到期扫描的受限
  SECURITY DEFINER 逃逸口（D26）。
- reviews.delivery_status：交付状态查询缓存（以 delivery_records 为准）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_phase4_delivery"
down_revision: str | None = "0004_phase3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_TENANT_TABLES = [
    "topic_files",
    "outbox_events",
    "deletion_requests",
    "export_jobs",
]

RLS_STATEMENTS: list[str] = []
for table in NEW_TENANT_TABLES:
    RLS_STATEMENTS.extend(
        [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY p_{table}_tenant ON {table} "
            "USING (workspace_id = app_workspace_id()) "
            "WITH CHECK (workspace_id = app_workspace_id())",
        ]
    )

STATEMENTS: list[str] = [
    # ------------------------------------------------------------ agent_runs
    "ALTER TABLE agent_runs ADD COLUMN trigger_type text NOT NULL DEFAULT 'manual'",
    "ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_trigger_type_check "
    "CHECK (trigger_type IN ('manual','schedule','api','chat'))",
    "CREATE INDEX idx_runs_trigger ON agent_runs (workspace_id, trigger_type, created_at DESC)",
    # ------------------------------------------------------------ git_connections
    "ALTER TABLE git_connections ADD COLUMN remote_url text",
    "ALTER TABLE git_connections ADD COLUMN api_base_url text",
    "ALTER TABLE git_connections ADD COLUMN credential_status text NOT NULL DEFAULT 'untested'",
    "ALTER TABLE git_connections ADD CONSTRAINT git_connections_credential_status_check "
    "CHECK (credential_status IN ('valid','invalid','untested'))",
    "ALTER TABLE git_connections ADD COLUMN last_tested_at timestamptz",
    "ALTER TABLE git_connections ADD COLUMN last_test_error text",
    "ALTER TABLE git_connections ADD CONSTRAINT git_connections_sync_interval_check "
    "CHECK (sync_interval_minutes BETWEEN 0 AND 1440)",
    # ------------------------------------------------------------ delivery_records
    "ALTER TABLE delivery_records ADD COLUMN manifest_json jsonb",
    "ALTER TABLE delivery_records ADD COLUMN diff_summary_json jsonb",
    "ALTER TABLE delivery_records ADD COLUMN error_code text",
    "ALTER TABLE delivery_records ADD COLUMN auto_merge boolean NOT NULL DEFAULT false",
    "ALTER TABLE delivery_records ADD COLUMN attempts int NOT NULL DEFAULT 0",
    "ALTER TABLE delivery_records ADD COLUMN content_hash text",
    "ALTER TABLE delivery_records ADD COLUMN base_branch text",
    "ALTER TABLE delivery_records ADD COLUMN merged_at timestamptz",
    "CREATE INDEX idx_delivery_open ON delivery_records (workspace_id, status) "
    "WHERE status IN ('pending','committing','committed','pr_open')",
    "CREATE UNIQUE INDEX uq_delivery_open_branch ON delivery_records (workspace_id, branch) "
    "WHERE branch IS NOT NULL AND status IN ('pending','committing','committed','pr_open')",
    # ------------------------------------------------------------ reviews cache
    "ALTER TABLE reviews ADD COLUMN delivery_status text",
    # ------------------------------------------------------------ topic_items
    "ALTER TABLE topic_items ADD COLUMN fields_json jsonb NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE topic_items ADD COLUMN conflicts_json jsonb NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE topic_items ADD COLUMN dropped_at timestamptz",
    "ALTER TABLE topic_items DROP CONSTRAINT topic_items_status_check",
    "ALTER TABLE topic_items ADD CONSTRAINT topic_items_status_check "
    "CHECK (status IN ('pending','open','resolved','stale','dropped'))",
    # ------------------------------------------------------------ topic_occurrences
    "UPDATE topic_occurrences SET line_start = 0 WHERE line_start IS NULL",
    "ALTER TABLE topic_occurrences ALTER COLUMN line_start SET DEFAULT 0",
    "ALTER TABLE topic_occurrences ALTER COLUMN line_start SET NOT NULL",
    "ALTER TABLE topic_occurrences ADD COLUMN review_id uuid",
    "ALTER TABLE topic_occurrences ADD COLUMN version int",
    "ALTER TABLE topic_occurrences ADD COLUMN review_entry_id uuid",
    "ALTER TABLE topic_occurrences ADD COLUMN fields_json jsonb NOT NULL DEFAULT '{}'::jsonb",
    "CREATE INDEX idx_topic_occurrences_review ON topic_occurrences "
    "(workspace_id, review_id, version)",
    # ------------------------------------------------------------ topic_files
    """
    CREATE TABLE topic_files (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      topic text NOT NULL CHECK (topic IN ('errors','methods','actions')),
      path text NOT NULL,
      auto_region_hash text,
      status text NOT NULL DEFAULT 'auto' CHECK (status IN ('auto','manual')),
      last_review_id uuid,
      last_version int,
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (workspace_id, topic)
    )
    """,
    # ------------------------------------------------------------ dispatcher
    """
    CREATE TABLE outbox_events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      topic text NOT NULL,
      payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','sent','failed','dead')),
      attempts int NOT NULL DEFAULT 0,
      available_at timestamptz NOT NULL DEFAULT now(),
      idempotency_key text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      sent_at timestamptz,
      UNIQUE (idempotency_key)
    )
    """,
    "CREATE INDEX idx_outbox_due ON outbox_events (status, available_at)",
    """
    CREATE TABLE dispatcher_heartbeats (
      id text PRIMARY KEY,
      last_tick_at timestamptz NOT NULL DEFAULT now(),
      version text NOT NULL DEFAULT '1'
    )
    """,
    # ------------------------------------------------------------ deletion / export
    """
    CREATE TABLE deletion_requests (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      scope text NOT NULL CHECK (scope IN ('workspace','account')),
      state text NOT NULL DEFAULT 'requested'
        CHECK (state IN ('requested','cooling','purged','cancelled')),
      purge_at timestamptz NOT NULL,
      requested_by uuid NOT NULL REFERENCES users(id),
      cancelled_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_deletion_due ON deletion_requests (purge_at) "
    "WHERE state = 'cooling'",
    "CREATE INDEX idx_deletion_workspace ON deletion_requests (workspace_id, state)",
    """
    CREATE TABLE export_jobs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      requested_by uuid NOT NULL REFERENCES users(id),
      status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','succeeded','failed','expired')),
      artifact_key text,
      expires_at timestamptz,
      error text,
      created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    # ------------------------------------------------------------ 受限调度扫描
    """
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mylife_dispatch') THEN
        CREATE ROLE mylife_dispatch NOLOGIN BYPASSRLS;
      END IF;
    END $$;
    """,
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
    """,
    "ALTER FUNCTION dispatch_due_schedules(int) OWNER TO mylife_dispatch",
    "GRANT USAGE ON SCHEMA public TO mylife_dispatch",
    "GRANT SELECT ON workspace_schedules TO mylife_dispatch",
    "REVOKE ALL ON FUNCTION dispatch_due_schedules(int) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION dispatch_due_schedules(int) TO mylife_app",
    """
    CREATE OR REPLACE FUNCTION dispatch_due_git_syncs(p_limit int)
    RETURNS TABLE (
      workspace_id uuid, conn_id uuid, sync_interval_minutes int, next_sync_at timestamptz
    )
    LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
      SELECT gc.workspace_id, gc.id, gc.sync_interval_minutes, gc.next_sync_at
      FROM git_connections gc
      WHERE gc.role = 'source' AND gc.sync_enabled
        AND gc.next_sync_at IS NOT NULL AND gc.next_sync_at <= now()
      ORDER BY gc.next_sync_at ASC
      LIMIT p_limit
    $$
    """,
    "ALTER FUNCTION dispatch_due_git_syncs(int) OWNER TO mylife_dispatch",
    "GRANT SELECT ON git_connections TO mylife_dispatch",
    "REVOKE ALL ON FUNCTION dispatch_due_git_syncs(int) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION dispatch_due_git_syncs(int) TO mylife_app",
]

# 新租户表的授权（与 0001 的默认权限一致；显式写出以便回滚审计）。
GRANTS = [
    "GRANT SELECT, INSERT, UPDATE, DELETE ON topic_files, outbox_events, "
    "deletion_requests, export_jobs TO mylife_app",
    "GRANT SELECT, INSERT, UPDATE ON dispatcher_heartbeats TO mylife_app",
    "GRANT SELECT, UPDATE ON workspace_schedules TO mylife_system",
]


def upgrade() -> None:
    for statement in STATEMENTS + RLS_STATEMENTS + GRANTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("REVOKE ALL ON FUNCTION dispatch_due_git_syncs(int) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION dispatch_due_schedules(int) FROM PUBLIC")
    op.execute("DROP FUNCTION IF EXISTS dispatch_due_git_syncs(int)")
    op.execute("DROP FUNCTION IF EXISTS dispatch_due_schedules(int)")
    op.execute("DROP TABLE IF EXISTS export_jobs CASCADE")
    op.execute("DROP TABLE IF EXISTS deletion_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS dispatcher_heartbeats CASCADE")
    op.execute("DROP TABLE IF EXISTS outbox_events CASCADE")
    op.execute("DROP TABLE IF EXISTS topic_files CASCADE")
    op.execute("DROP INDEX IF EXISTS idx_topic_occurrences_review")
    for column in ("fields_json", "review_entry_id", "version", "review_id"):
        op.execute(f"ALTER TABLE topic_occurrences DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE topic_occurrences ALTER COLUMN line_start DROP NOT NULL")
    op.execute("ALTER TABLE topic_occurrences ALTER COLUMN line_start DROP DEFAULT")
    op.execute("ALTER TABLE topic_items DROP CONSTRAINT IF EXISTS topic_items_status_check")
    op.execute(
        "ALTER TABLE topic_items ADD CONSTRAINT topic_items_status_check "
        "CHECK (status IN ('pending','open','resolved','stale'))"
    )
    for column in ("dropped_at", "conflicts_json", "fields_json"):
        op.execute(f"ALTER TABLE topic_items DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE reviews DROP COLUMN IF EXISTS delivery_status")
    op.execute("DROP INDEX IF EXISTS uq_delivery_open_branch")
    op.execute("DROP INDEX IF EXISTS idx_delivery_open")
    for column in (
        "merged_at",
        "base_branch",
        "content_hash",
        "attempts",
        "auto_merge",
        "error_code",
        "diff_summary_json",
        "manifest_json",
    ):
        op.execute(f"ALTER TABLE delivery_records DROP COLUMN IF EXISTS {column}")
    op.execute(
        "ALTER TABLE git_connections DROP CONSTRAINT IF EXISTS "
        "git_connections_sync_interval_check"
    )
    for column in (
        "last_test_error",
        "last_tested_at",
        "credential_status",
        "api_base_url",
        "remote_url",
    ):
        op.execute(f"ALTER TABLE git_connections DROP COLUMN IF EXISTS {column}")
    op.execute(
        "ALTER TABLE git_connections DROP CONSTRAINT IF EXISTS "
        "git_connections_credential_status_check"
    )
    op.execute("DROP INDEX IF EXISTS idx_runs_trigger")
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_trigger_type_check")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS trigger_type")
