"""Phase 2：chat 运行时（agent_runs / run_steps / run_events）与会话软删除

Revision ID: 0003_phase2
Revises: 0002_phase1
Create Date: 2026-11-07

对齐：DATA-MODEL §3.9/§4.1–4.3、ARCHITECTURE §4.3/§4.6、API-CONTRACTS §3.6/§6。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_phase2"
down_revision: str | None = "0002_phase1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS: list[str] = [
    """CREATE OR REPLACE FUNCTION is_active_state(state text) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS $$
      SELECT state IN (
        'created','snapshotting','planning','retrieving','drafting','reviewing',
        'revising','human_gate','delivering','retry_wait','needs_human'
      )
    $$""",
    """
    CREATE TABLE agent_runs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      kind text NOT NULL CHECK (kind IN ('chat','daily','weekly','yearly')),
      period_key text,
      conversation_id uuid REFERENCES conversations(id) ON DELETE CASCADE,
      client_turn_id uuid,
      state text NOT NULL,
      state_payload jsonb NOT NULL DEFAULT '{}',
      attempt_id uuid,
      idem_key text NOT NULL,
      lease_owner text,
      lease_expires_at timestamptz,
      retry_from_state text,
      snapshot_id uuid,
      version int,
      supersedes_run_id uuid REFERENCES agent_runs(id),
      trace_id text,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz,
      CHECK (kind <> 'chat' OR period_key IS NULL),
      CHECK (kind = 'chat' OR period_key IS NOT NULL),
      CHECK (kind <> 'chat' OR conversation_id IS NOT NULL),
      UNIQUE (workspace_id, idem_key)
    )
    """,
    "CREATE UNIQUE INDEX uq_runs_review_active ON agent_runs (workspace_id, kind, period_key) "
    "WHERE kind IN ('daily','weekly','yearly') AND is_active_state(state)",
    "CREATE UNIQUE INDEX uq_runs_chat_serial ON agent_runs (workspace_id, conversation_id) "
    "WHERE kind = 'chat' AND state IN ('created','retrieving','drafting','verifying')",
    "CREATE UNIQUE INDEX uq_runs_chat_turn ON agent_runs "
    "(workspace_id, conversation_id, client_turn_id) WHERE kind = 'chat'",
    "CREATE INDEX idx_runs_ws_created ON agent_runs (workspace_id, created_at DESC)",
    """
    CREATE TABLE run_steps (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      run_id uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
      seq int NOT NULL,
      role text NOT NULL CHECK (role IN ('planner','worker','writer','reviewer','system')),
      tool_name text,
      status text NOT NULL CHECK (status IN ('started','succeeded','failed','skipped')),
      input_digest text,
      output_digest text,
      model text,
      tokens_in int NOT NULL DEFAULT 0,
      tokens_out int NOT NULL DEFAULT 0,
      latency_ms int,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (run_id, seq)
    )
    """,
    "CREATE INDEX idx_run_steps_run ON run_steps (run_id, seq)",
    """
    CREATE TABLE run_events (
      event_id bigserial PRIMARY KEY,
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      run_id uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
      from_state text,
      to_state text,
      event text NOT NULL,
      payload jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_run_events_run ON run_events (run_id, event_id)",
    "CREATE INDEX idx_run_events_ws ON run_events (workspace_id, created_at)",
    "GRANT USAGE, SELECT ON SEQUENCE run_events_event_id_seq TO mylife_app",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "GRANT USAGE, SELECT ON SEQUENCES TO mylife_app",
    "ALTER TABLE conversations ADD COLUMN deleted_at timestamptz",
    "CREATE INDEX idx_conversations_ws_updated ON conversations "
    "(workspace_id, updated_at DESC, id DESC) WHERE deleted_at IS NULL",
    "ALTER TABLE messages ADD COLUMN usage_json jsonb",
    # RLS
    "ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE agent_runs FORCE ROW LEVEL SECURITY",
    "CREATE POLICY p_agent_runs_tenant ON agent_runs "
    "USING (workspace_id = app_workspace_id()) WITH CHECK (workspace_id = app_workspace_id())",
    "ALTER TABLE run_steps ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE run_steps FORCE ROW LEVEL SECURITY",
    "CREATE POLICY p_run_steps_tenant ON run_steps "
    "USING (workspace_id = app_workspace_id()) WITH CHECK (workspace_id = app_workspace_id())",
    "ALTER TABLE run_events ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE run_events FORCE ROW LEVEL SECURITY",
    "CREATE POLICY p_run_events_tenant ON run_events "
    "USING (workspace_id = app_workspace_id()) WITH CHECK (workspace_id = app_workspace_id())",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in ["run_events", "run_steps", "agent_runs"]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS usage_json")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS deleted_at")
    op.execute("DROP FUNCTION IF EXISTS is_active_state(text)")
