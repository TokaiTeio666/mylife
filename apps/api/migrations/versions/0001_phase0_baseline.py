"""Phase 0 基线：数据模型 + RLS 骨架

Revision ID: 0001_phase0
Revises:
Create Date: 2026-11-06

对齐：DATA-MODEL.md §3/§4/§5/§6、DIRECTORY-CONSTRAINTS.md §2、ARCHITECTURE.md §6。

实现说明（与文档的偏差已登记在 docs/IMPLEMENTATION-NOTES.md）：
- `users.is_platform_admin`：API-CONTRACTS §3.10 需要平台管理员标记，但 DATA-MODEL §3.1
  未定义该列；以最小扩展列实现。
- `workspaces` 的 SELECT 策略允许“我是成员的 workspace”，否则 GET /workspaces 在 RLS 下
  永远只能看到当前 active workspace，无法满足 AC-03/AC-04。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_phase0"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS: list[str] = [
    # ---------------------------------------------------------------- 扩展
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    "CREATE EXTENSION IF NOT EXISTS vector",
    # ------------------------------------------------ 角色（测试环境兜底；生产由 init 创建）
    """
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mylife_app') THEN
        EXECUTE 'CREATE ROLE mylife_app NOLOGIN NOINHERIT NOBYPASSRLS';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mylife_system') THEN
        EXECUTE 'CREATE ROLE mylife_system NOLOGIN NOINHERIT NOBYPASSRLS';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mylife_migrator') THEN
        EXECUTE 'CREATE ROLE mylife_migrator NOLOGIN';
      END IF;
    END $$;
    """,
    # ---------------------------------------------------------------- RLS 辅助函数
    """
    CREATE OR REPLACE FUNCTION app_workspace_id() RETURNS uuid
    LANGUAGE sql STABLE AS $$
      SELECT NULLIF(current_setting('app.workspace_id', true), '')::uuid
    $$
    """,
    # 成员级读取（GET /workspaces）需要；无值时返回 NULL，策略默认拒绝。
    """
    CREATE OR REPLACE FUNCTION app_user_id() RETURNS uuid
    LANGUAGE sql STABLE AS $$
      SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
    $$
    """,
    # ---------------------------------------------------------------- 基线表
    """
    CREATE TABLE users (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      email text NOT NULL,
      password_hash text NOT NULL,
      display_name text NOT NULL,
      status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','disabled','deleted')),
      is_platform_admin boolean NOT NULL DEFAULT false,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))",
    """
    CREATE TABLE workspaces (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      name text NOT NULL,
      slug text NOT NULL,
      owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      plan text NOT NULL DEFAULT 'free' CHECK (plan IN ('free','pro')),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (slug)
    )
    """,
    """
    CREATE TABLE workspace_members (
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      role text NOT NULL CHECK (role IN ('owner','admin','member')),
      joined_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (workspace_id, user_id)
    )
    """,
    """
    CREATE TABLE sessions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      token_hash text NOT NULL,
      token_version int NOT NULL DEFAULT 1,
      active_workspace_id uuid REFERENCES workspaces(id),
      expires_at timestamptz NOT NULL,
      revoked_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (token_hash)
    )
    """,
    "CREATE INDEX idx_sessions_user ON sessions (user_id, expires_at)",
    """
    CREATE TABLE invite_codes (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      code text NOT NULL,
      created_by uuid NOT NULL REFERENCES users(id),
      used_by uuid REFERENCES users(id),
      expires_at timestamptz,
      used_at timestamptz,
      note text,
      UNIQUE (code)
    )
    """,
    """
    CREATE TABLE audit_logs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid,
      user_id uuid,
      action text NOT NULL,
      target text,
      detail jsonb NOT NULL DEFAULT '{}',
      ip inet,
      created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_audit_ws_created ON audit_logs (workspace_id, created_at DESC)",
    "CREATE INDEX idx_audit_action ON audit_logs (action, created_at DESC)",
    """
    CREATE TABLE job_runs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      kind text NOT NULL,
      period_key text,
      status text NOT NULL
        CHECK (status IN ('queued','running','succeeded','partial','failed','skipped','cancelled')),
      error text,
      run_key text,
      state text,
      agent_run_id uuid,
      started_at timestamptz,
      finished_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE workspace_settings (
      workspace_id uuid PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
      timezone text NOT NULL DEFAULT 'Asia/Shanghai',
      daily_at time NOT NULL DEFAULT '01:00',
      weekly_at time NOT NULL DEFAULT '01:30',
      yearly_at time NOT NULL DEFAULT '02:00',
      reviews_root text NOT NULL DEFAULT 'wiki/复盘',
      delivery_policy_json jsonb NOT NULL DEFAULT '{}',
      auto_merge_daily boolean NOT NULL DEFAULT false,
      retrieval_top_k int NOT NULL DEFAULT 20 CHECK (retrieval_top_k BETWEEN 1 AND 50),
      context_window_tokens int NOT NULL DEFAULT 32000
        CHECK (context_window_tokens BETWEEN 4000 AND 200000),
      web_search_enabled boolean NOT NULL DEFAULT false,
      embedding_profile_id uuid,
      updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE workspace_schedules (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      kind text NOT NULL CHECK (kind IN ('daily','weekly','yearly')),
      local_time time NOT NULL,
      timezone text NOT NULL,
      next_run_at timestamptz,
      catchup_days int NOT NULL DEFAULT 7 CHECK (catchup_days BETWEEN 0 AND 30),
      enabled boolean NOT NULL DEFAULT true,
      last_skip_reason text
        CHECK (last_skip_reason IN ('catchup_window_exceeded','period_already_succeeded',NULL)),
      UNIQUE (workspace_id, kind)
    )
    """,
    "CREATE INDEX idx_schedules_next ON workspace_schedules (next_run_at) WHERE enabled",
    """
    CREATE TABLE documents (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      path text NOT NULL,
      title text NOT NULL,
      content text NOT NULL,
      frontmatter_json jsonb NOT NULL DEFAULT '{}',
      content_hash text NOT NULL,
      size bigint NOT NULL,
      mtime timestamptz,
      indexed_at timestamptz,
      tags text[] NOT NULL DEFAULT '{}',
      extension text NOT NULL DEFAULT 'md',
      search_text text NOT NULL DEFAULT '',
      tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
      occurred_on date,
      source_type text NOT NULL DEFAULT 'user' CHECK (source_type IN ('user','generated')),
      UNIQUE (workspace_id, path),
      UNIQUE (workspace_id, id)
    )
    """,
    "CREATE INDEX idx_documents_tsv ON documents USING gin (tsv)",
    "CREATE INDEX idx_documents_tags ON documents USING gin (tags)",
    "CREATE INDEX idx_documents_occurred ON documents (workspace_id, occurred_on)",
    "CREATE INDEX idx_documents_extension ON documents (workspace_id, extension)",
    "CREATE INDEX idx_documents_path ON documents (path text_pattern_ops)",
    """
    CREATE TABLE document_chunks (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL,
      document_id uuid NOT NULL,
      heading text,
      content text NOT NULL,
      token_count int NOT NULL,
      chunk_ordinal int NOT NULL,
      chunk_hash text NOT NULL,
      embedding vector(1024),
      embedding_profile_id uuid,
      UNIQUE (document_id, chunk_ordinal),
      FOREIGN KEY (workspace_id, document_id)
        REFERENCES documents (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX idx_chunks_embedding ON document_chunks
      USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
    """,
    """
    CREATE TABLE document_links (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL,
      source_document_id uuid NOT NULL,
      target_path text NOT NULL,
      link_text text,
      UNIQUE (source_document_id, target_path, link_text),
      FOREIGN KEY (workspace_id, source_document_id)
        REFERENCES documents (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE tasks (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      key text NOT NULL,
      text text NOT NULL,
      status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','done','carried','dropped')),
      first_seen date NOT NULL,
      last_seen date NOT NULL,
      done_date date,
      source_path text,
      line_number int,
      UNIQUE (workspace_id, key)
    )
    """,
    "CREATE INDEX idx_tasks_status ON tasks (workspace_id, status, first_seen)",
    """
    CREATE TABLE reviews (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      kind text NOT NULL CHECK (kind IN ('daily','weekly','yearly')),
      period_key text NOT NULL,
      title text NOT NULL,
      content text NOT NULL,
      model text,
      generated_at timestamptz NOT NULL DEFAULT now(),
      version int NOT NULL DEFAULT 1,
      content_hash text,
      git_path text,
      status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','in_review','published','superseded','manual')),
      superseded_by uuid REFERENCES reviews(id),
      UNIQUE (workspace_id, kind, period_key, version)
    )
    """,
    """
    CREATE TABLE conversations (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title text NOT NULL DEFAULT '新对话',
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (workspace_id, id)
    )
    """,
    """
    CREATE TABLE messages (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL,
      conversation_id uuid NOT NULL,
      run_id uuid,
      role text NOT NULL CHECK (role IN ('user','assistant','system','tool')),
      content text NOT NULL,
      references_json jsonb NOT NULL DEFAULT '[]',
      tool_events_json jsonb NOT NULL DEFAULT '[]',
      status text NOT NULL DEFAULT 'complete'
        CHECK (status IN ('drafting','complete','partial','cancelled')),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (conversation_id, created_at, id),
      FOREIGN KEY (workspace_id, conversation_id)
        REFERENCES conversations (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE usage_events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      user_id uuid,
      kind text NOT NULL,
      model text,
      prompt_tokens int NOT NULL DEFAULT 0,
      completion_tokens int NOT NULL DEFAULT 0,
      latency_ms int,
      cost numeric(12,6) NOT NULL DEFAULT 0,
      degraded boolean NOT NULL DEFAULT false,
      created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_usage_ws_created ON usage_events (workspace_id, created_at DESC)",
    """
    CREATE TABLE provider_credentials (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      provider text NOT NULL CHECK (provider IN ('llm','embedding')),
      key_ciphertext bytea NOT NULL,
      key_version int NOT NULL,
      status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','invalid','disabled')),
      last_tested_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (workspace_id, provider)
    )
    """,
    # ---------------------------------------------------------------- RLS
    """
    DO $$
    DECLARE t text;
    BEGIN
      FOREACH t IN ARRAY ARRAY[
        'documents','document_chunks','document_links','tasks','reviews',
        'conversations','messages','usage_events','job_runs',
        'workspace_settings','workspace_schedules','provider_credentials'
      ]
      LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
          'CREATE POLICY p_%s_tenant ON %I USING (workspace_id = app_workspace_id()) '
          'WITH CHECK (workspace_id = app_workspace_id())',
          t, t
        );
      END LOOP;
    END $$;
    """,
    # workspaces：成员可读自己所属的全部 workspace（支撑 GET /workspaces 与切换）。
    "ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE workspaces FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY p_workspaces_select ON workspaces FOR SELECT
      USING (
        id = app_workspace_id()
        OR id IN (
          SELECT wm.workspace_id FROM workspace_members wm WHERE wm.user_id = app_user_id()
        )
      )
    """,
    """
    CREATE POLICY p_workspaces_insert ON workspaces FOR INSERT
      WITH CHECK (id = app_workspace_id())
    """,
    """
    CREATE POLICY p_workspaces_update ON workspaces FOR UPDATE
      USING (id = app_workspace_id()) WITH CHECK (id = app_workspace_id())
    """,
    # workspace_members：活跃 workspace 内可见；本人成员行跨 workspace 可见（用于列举）。
    "ALTER TABLE workspace_members ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE workspace_members FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY p_members_select ON workspace_members FOR SELECT
      USING (workspace_id = app_workspace_id() OR user_id = app_user_id())
    """,
    """
    CREATE POLICY p_members_insert ON workspace_members FOR INSERT
      WITH CHECK (workspace_id = app_workspace_id())
    """,
    """
    CREATE POLICY p_members_update ON workspace_members FOR UPDATE
      USING (workspace_id = app_workspace_id())
      WITH CHECK (workspace_id = app_workspace_id())
    """,
    """
    CREATE POLICY p_members_delete ON workspace_members FOR DELETE
      USING (workspace_id = app_workspace_id())
    """,
    # audit_logs：只追加；租户只读自己 workspace，平台审计（workspace_id IS NULL）仅管理员角色可读。
    "ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY p_audit_select ON audit_logs FOR SELECT
      USING (workspace_id = app_workspace_id())
    """,
    """
    CREATE POLICY p_audit_insert ON audit_logs FOR INSERT
      WITH CHECK (workspace_id IS NULL OR workspace_id = app_workspace_id())
    """,
    # ---------------------------------------------------------------- 授权
    "GRANT USAGE ON SCHEMA public TO mylife_app, mylife_system",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mylife_app",
    "GRANT EXECUTE ON FUNCTION app_workspace_id() TO mylife_app, mylife_system",
    "GRANT EXECUTE ON FUNCTION app_user_id() TO mylife_app, mylife_system",
    "GRANT SELECT, UPDATE ON workspace_schedules TO mylife_system",
    "GRANT SELECT ON workspaces, sessions TO mylife_system",
    """
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mylife_app
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in [
        "provider_credentials",
        "usage_events",
        "messages",
        "conversations",
        "reviews",
        "tasks",
        "document_links",
        "document_chunks",
        "documents",
        "workspace_schedules",
        "workspace_settings",
        "job_runs",
        "audit_logs",
        "invite_codes",
        "sessions",
        "workspace_members",
        "workspaces",
        "users",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS app_user_id()")
    op.execute("DROP FUNCTION IF EXISTS app_workspace_id()")
