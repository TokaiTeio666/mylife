"""Phase 3：复盘运行时数据模型（review_* / evidence / topics / webhooks）

Revision ID: 0004_phase3
Revises: 0003_phase2
Create Date: 2026-11-07

对齐：ARCHITECTURE §5.1/§6/§9、DATA-MODEL §4.8–4.17、API-CONTRACTS §3.7/§7、
PHASE3-SPEC §2。

与文档的偏差（登记 docs/IMPLEMENTATION-NOTES.md §2C）：
- reviews 增加 snapshot_id/degraded/degraded_reasons/verdict_json 与 UNIQUE(workspace_id,id)；
  status CHECK 扩为 DATA-MODEL 与 API 两套取值的并集。
- agent_runs.review_id：run → review 直连。
- run_events.stream_epoch：API-CONTRACTS §7.2 要求，0003 未建。
- usage_events.run_id/attempt_id/role：预算按 run 累计（PHASE3-SPEC D4）。
- documents.parse_warnings：日期来源冲突 warning（PHASE3-SPEC D15）。
- git_connections.webhook_secret_ciphertext：SEC-16 验签。
- is_active_state 移除 needs_human（API-CONTRACTS §7.1 中 needs_human 是终态）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_phase3"
down_revision: str | None = "0003_phase2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_TENANT_TABLES = [
    "evidence_items",
    "review_sections",
    "review_entries",
    "review_citations",
    "review_versions",
    "review_search",
    "topic_items",
    "topic_occurrences",
    "delivery_records",
    "delivery_events",
    "review_confirmations",
    "idempotency_keys",
]

STATEMENTS: list[str] = [
    # ------------------------------------------------------------ 函数修正
    """CREATE OR REPLACE FUNCTION is_active_state(state text) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS $$
      SELECT state IN (
        'created','snapshotting','planning','retrieving','drafting','reviewing',
        'revising','human_gate','delivering','retry_wait'
      )
    $$""",
    # ------------------------------------------------------------ reviews 扩展
    "ALTER TABLE reviews ADD CONSTRAINT uq_reviews_ws_id UNIQUE (workspace_id, id)",
    "ALTER TABLE reviews ADD COLUMN snapshot_id uuid REFERENCES source_snapshots(id)",
    "ALTER TABLE reviews ADD COLUMN degraded boolean NOT NULL DEFAULT false",
    "ALTER TABLE reviews ADD COLUMN degraded_reasons text[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE reviews ADD COLUMN verdict_json jsonb",
    "ALTER TABLE reviews DROP CONSTRAINT IF EXISTS reviews_status_check",
    "ALTER TABLE reviews ADD CONSTRAINT reviews_status_check CHECK (status IN ("
    "'draft','in_review','published','superseded','manual',"
    "'generating','needs_human','approved','delivered','failed','security_abort'))",
    "ALTER TABLE reviews ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()",
    # ------------------------------------------------------------ agent_runs 关联
    "ALTER TABLE agent_runs ADD COLUMN review_id uuid REFERENCES reviews(id)",
    "CREATE INDEX idx_agent_runs_review ON agent_runs (workspace_id, review_id)",
    # ------------------------------------------------------------ 事件/用量/文档
    "ALTER TABLE run_events ADD COLUMN stream_epoch int NOT NULL DEFAULT 0",
    "ALTER TABLE usage_events ADD COLUMN run_id uuid",
    "ALTER TABLE usage_events ADD COLUMN attempt_id uuid",
    "ALTER TABLE usage_events ADD COLUMN role text",
    "CREATE INDEX idx_usage_run ON usage_events (workspace_id, run_id, created_at)",
    "ALTER TABLE documents ADD COLUMN parse_warnings jsonb NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE git_connections ADD COLUMN webhook_secret_ciphertext bytea",
    # ------------------------------------------------------------ evidence_items
    """
    CREATE TABLE evidence_items (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      run_id uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
      evidence_id text NOT NULL,
      snapshot_id uuid NOT NULL REFERENCES source_snapshots(id),
      source_type text NOT NULL
        CHECK (source_type IN ('document','chunk','review','task','stats')),
      document_id uuid,
      chunk_id uuid,
      source_path text,
      source_date date,
      occurred_on date,
      document_content_hash text,
      heading text,
      tags text[] NOT NULL DEFAULT '{}',
      line_start int,
      line_end int,
      quote text NOT NULL,
      quote_hash text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      CHECK (source_type <> 'stats' OR source_path IS NULL),
      CHECK (source_type NOT IN ('document','chunk','review') OR document_content_hash IS NOT NULL),
      UNIQUE (run_id, evidence_id)
    )
    """,
    "CREATE INDEX idx_evidence_run_type ON evidence_items (run_id, source_type)",
    # ------------------------------------------------------------ review_sections
    """
    CREATE TABLE review_sections (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      review_id uuid NOT NULL,
      version int NOT NULL,
      section_key text NOT NULL,
      ordinal int NOT NULL,
      content_md text NOT NULL,
      structured_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (review_id, version, section_key),
      UNIQUE (review_id, version, ordinal),
      FOREIGN KEY (workspace_id, review_id) REFERENCES reviews (workspace_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_review_sections_ver ON review_sections (review_id, version)",
    # ------------------------------------------------------------ review_entries
    """
    CREATE TABLE review_entries (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      review_id uuid NOT NULL,
      version int NOT NULL,
      section_key text NOT NULL,
      item_type text NOT NULL CHECK (item_type IN ('error','method','action')),
      canonical_key text NOT NULL,
      fields_json jsonb NOT NULL,
      source_path text,
      line_start int,
      line_end int,
      evidence_id text,
      source_date date,
      confidence numeric(3,2),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (review_id, version, section_key, item_type, canonical_key),
      FOREIGN KEY (workspace_id, review_id) REFERENCES reviews (workspace_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_review_entries_key ON review_entries (workspace_id, item_type, canonical_key)",
    "CREATE INDEX idx_review_entries_ver ON review_entries (review_id, version)",
    # ------------------------------------------------------------ review_citations
    """
    CREATE TABLE review_citations (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      review_id uuid NOT NULL,
      version int NOT NULL,
      claim_id text NOT NULL,
      evidence_id text NOT NULL,
      source_path text,
      source_date date,
      document_content_hash text,
      line_start int,
      line_end int,
      quote text NOT NULL,
      quote_hash text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (review_id, version, claim_id, evidence_id),
      FOREIGN KEY (workspace_id, review_id) REFERENCES reviews (workspace_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_review_citations_ver ON review_citations (review_id, version)",
    # ------------------------------------------------------------ review_versions
    """
    CREATE TABLE review_versions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      review_id uuid NOT NULL,
      version int NOT NULL,
      content_md text NOT NULL,
      content_hash text NOT NULL,
      git_sha text,
      model text,
      prompt_version text,
      snapshot_id uuid REFERENCES source_snapshots(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (review_id, version),
      FOREIGN KEY (workspace_id, review_id) REFERENCES reviews (workspace_id, id) ON DELETE CASCADE
    )
    """,
    # ------------------------------------------------------------ review_search
    """
    CREATE TABLE review_search (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      review_id uuid NOT NULL,
      version int NOT NULL,
      kind text NOT NULL,
      period_key text NOT NULL,
      title text NOT NULL,
      path text NOT NULL,
      search_text text NOT NULL DEFAULT '',
      tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
      occurred_on date,
      content_hash text NOT NULL,
      is_current boolean NOT NULL DEFAULT true,
      UNIQUE (review_id, version),
      FOREIGN KEY (workspace_id, review_id) REFERENCES reviews (workspace_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE UNIQUE INDEX uq_review_search_current ON review_search "
    "(workspace_id, kind, period_key) WHERE is_current",
    "CREATE INDEX idx_review_search_tsv ON review_search USING gin (tsv)",
    "CREATE INDEX idx_review_search_date ON review_search (workspace_id, occurred_on)",
    # ------------------------------------------------------------ topic_items
    """
    CREATE TABLE topic_items (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      topic text NOT NULL CHECK (topic IN ('errors','methods','actions')),
      canonical_key text NOT NULL,
      title text NOT NULL,
      first_seen date NOT NULL,
      last_seen date NOT NULL,
      occurrence_count int NOT NULL DEFAULT 0,
      status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','open','resolved','stale')),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (workspace_id, topic, canonical_key)
    )
    """,
    "CREATE INDEX idx_topic_items_status ON topic_items (workspace_id, topic, status)",
    """
    CREATE TABLE topic_occurrences (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      item_id uuid NOT NULL REFERENCES topic_items(id) ON DELETE CASCADE,
      source_date date NOT NULL,
      source_path text NOT NULL,
      line_start int,
      line_end int,
      quote text NOT NULL,
      evidence_id text,
      UNIQUE (item_id, source_date, source_path, line_start)
    )
    """,
    # ------------------------------------------------------------ delivery (Phase 4)
    """
    CREATE TABLE delivery_records (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      review_id uuid NOT NULL,
      version int NOT NULL,
      target text NOT NULL CHECK (target IN ('git','object_store','db_only','ui_diff')),
      provider text,
      branch text,
      commit_sha text,
      pr_number int,
      pr_url text,
      status text NOT NULL CHECK (status IN (
        'pending','blocked','committing','pr_open','merged','conflict','failed','noop',
        'committed','closed')),
      merged_commit text,
      degraded boolean NOT NULL DEFAULT false,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (workspace_id, review_id, version, target),
      FOREIGN KEY (workspace_id, review_id) REFERENCES reviews (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE delivery_events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      provider_delivery_id text NOT NULL,
      conn_id uuid REFERENCES git_connections(id),
      event text NOT NULL,
      payload_digest text NOT NULL,
      status text NOT NULL DEFAULT 'received'
        CHECK (status IN ('received','handled','ignored','failed')),
      received_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (provider_delivery_id)
    )
    """,
    # ------------------------------------------------------------ 准入辅助表
    """
    CREATE TABLE review_confirmations (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      token_hash text NOT NULL,
      kind text NOT NULL CHECK (kind IN ('daily','weekly','yearly')),
      period_key text NOT NULL,
      expires_at timestamptz NOT NULL,
      consumed_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (token_hash)
    )
    """,
    """
    CREATE TABLE idempotency_keys (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      endpoint text NOT NULL,
      key text NOT NULL,
      request_hash text NOT NULL,
      response_status int,
      response_body jsonb,
      created_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL,
      UNIQUE (workspace_id, user_id, endpoint, key)
    )
    """,
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


def upgrade() -> None:
    for statement in STATEMENTS + RLS_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in reversed(NEW_TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE git_connections DROP COLUMN IF EXISTS webhook_secret_ciphertext")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS parse_warnings")
    op.execute("DROP INDEX IF EXISTS idx_usage_run")
    for column in ("role", "attempt_id", "run_id"):
        op.execute(f"ALTER TABLE usage_events DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE run_events DROP COLUMN IF EXISTS stream_epoch")
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_review")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS review_id")
    op.execute("ALTER TABLE reviews DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE reviews DROP CONSTRAINT IF EXISTS reviews_status_check")
    op.execute(
        "ALTER TABLE reviews ADD CONSTRAINT reviews_status_check "
        "CHECK (status IN ('draft','in_review','published','superseded','manual'))"
    )
    for column in ("verdict_json", "degraded_reasons", "degraded", "snapshot_id"):
        op.execute(f"ALTER TABLE reviews DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE reviews DROP CONSTRAINT IF EXISTS uq_reviews_ws_id")
    op.execute(
        """CREATE OR REPLACE FUNCTION is_active_state(state text) RETURNS boolean
        LANGUAGE sql IMMUTABLE AS $$
          SELECT state IN (
            'created','snapshotting','planning','retrieving','drafting','reviewing',
            'revising','human_gate','delivering','retry_wait','needs_human'
          )
        $$"""
    )
