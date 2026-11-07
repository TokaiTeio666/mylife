"""Phase 1：接入层、快照、变更历史、Embedding 与 Git 连接

Revision ID: 0002_phase1
Revises: 0001_phase0
Create Date: 2026-11-07

对齐：DATA-MODEL §3.4/3.5/3.6、§4.4–4.7、§4.19/4.23；ARCHITECTURE §2.1/§2.6。

扩展列（已登记 docs/IMPLEMENTATION-NOTES.md）：
- documents.raw_hash / parser_version / tokenizer_version：增量导入在解析前判断
  原始文件是否变化（AC-12 “parser 调用增量 0”）。
- document_chunks.line_start/line_end：搜索结果的 line_range。
- job_runs.progress_json/counters_json/failed_items_json/payload_json/parent_job_id/
  snapshot_id：导入进度、失败明细与重试链（API-CONTRACTS §3.3）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_phase1"
down_revision: str | None = "0001_phase0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS: list[str] = [
    # ---------------------------------------------------------------- 快照
    """
    CREATE TABLE source_snapshots (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      commit_sha text,
      manifest_digest text NOT NULL,
      doc_count int NOT NULL,
      as_of timestamptz NOT NULL,
      storage_path text,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (workspace_id, commit_sha)
    )
    """,
    """
    CREATE TABLE snapshot_documents (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      snapshot_id uuid NOT NULL REFERENCES source_snapshots(id) ON DELETE CASCADE,
      document_id uuid NOT NULL,
      path text NOT NULL,
      content_hash text NOT NULL,
      UNIQUE (snapshot_id, document_id)
    )
    """,
    "CREATE INDEX idx_snapshot_docs_snap ON snapshot_documents (snapshot_id)",
    """
    CREATE TABLE document_changes (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      document_id uuid NOT NULL,
      change_type text NOT NULL CHECK (change_type IN ('added','modified','deleted','renamed')),
      source_date date,
      commit_sha text,
      created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_doc_changes_date ON document_changes (workspace_id, source_date)",
    """
    CREATE TABLE task_occurrences (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      task_id uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      document_id uuid NOT NULL,
      occurrence_date date NOT NULL,
      checked boolean NOT NULL,
      line_number int NOT NULL,
      raw_text text NOT NULL,
      UNIQUE (workspace_id, document_id, occurrence_date, line_number)
    )
    """,
    "CREATE INDEX idx_task_occ_task ON task_occurrences (task_id, occurrence_date)",
    # ---------------------------------------------------------------- Embedding
    """
    CREATE TABLE embedding_profiles (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      provider text NOT NULL,
      model text NOT NULL,
      dimension int NOT NULL CHECK (dimension > 0),
      index_name text NOT NULL,
      status text NOT NULL DEFAULT 'building'
        CHECK (status IN ('active','building','retired')),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (workspace_id, model, dimension)
    )
    """,
    "CREATE UNIQUE INDEX uq_embedding_active ON embedding_profiles (workspace_id) "
    "WHERE status = 'active'",
    # ---------------------------------------------------------------- Git 连接
    """
    CREATE TABLE git_connections (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      role text NOT NULL CHECK (role IN ('source','output')),
      provider text NOT NULL CHECK (provider IN ('github','gitlab','generic')),
      repo_full_name text NOT NULL,
      base_branch text NOT NULL DEFAULT 'main',
      auth_type text NOT NULL CHECK (auth_type IN ('github_app','deploy_key','pat')),
      credential_ciphertext bytea,
      credential_key_version int,
      reviews_root text,
      mode text CHECK (mode IN ('managed','vault_writeback','separate')),
      sync_enabled boolean NOT NULL DEFAULT false,
      sync_interval_minutes int NOT NULL DEFAULT 0 CHECK (sync_interval_minutes >= 0),
      next_sync_at timestamptz,
      last_sync_at timestamptz,
      last_sync_status text CHECK (last_sync_status IN ('ok','failed','skipped')),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (workspace_id, provider, repo_full_name, role)
    )
    """,
    "CREATE INDEX idx_git_sync_next ON git_connections (next_sync_at) "
    "WHERE sync_enabled AND role = 'source'",
    # ---------------------------------------------------------------- 扩展列
    "ALTER TABLE documents ADD COLUMN raw_hash text",
    "ALTER TABLE documents ADD COLUMN parser_version text",
    "ALTER TABLE documents ADD COLUMN tokenizer_version text",
    "ALTER TABLE document_chunks ADD COLUMN line_start int",
    "ALTER TABLE document_chunks ADD COLUMN line_end int",
    "CREATE INDEX idx_chunks_document ON document_chunks (document_id)",
    "ALTER TABLE job_runs ADD COLUMN progress_json jsonb NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE job_runs ADD COLUMN counters_json jsonb NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE job_runs ADD COLUMN failed_items_json jsonb NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE job_runs ADD COLUMN payload_json jsonb NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE job_runs ADD COLUMN parent_job_id uuid",
    "ALTER TABLE job_runs ADD COLUMN snapshot_id uuid REFERENCES source_snapshots(id)",
    "CREATE INDEX idx_job_runs_ws_created ON job_runs (workspace_id, created_at DESC)",
    # ---------------------------------------------------------------- RLS
    "ALTER TABLE source_snapshots ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE source_snapshots FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY p_source_snapshots_tenant ON source_snapshots
      USING (workspace_id = app_workspace_id()) WITH CHECK (workspace_id = app_workspace_id())
    """,
    "ALTER TABLE snapshot_documents ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE snapshot_documents FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY p_snapshot_documents_tenant ON snapshot_documents
      USING (workspace_id = app_workspace_id()) WITH CHECK (workspace_id = app_workspace_id())
    """,
    "ALTER TABLE document_changes ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE document_changes FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY p_document_changes_tenant ON document_changes
      USING (workspace_id = app_workspace_id()) WITH CHECK (workspace_id = app_workspace_id())
    """,
    "ALTER TABLE task_occurrences ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE task_occurrences FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY p_task_occurrences_tenant ON task_occurrences
      USING (workspace_id = app_workspace_id()) WITH CHECK (workspace_id = app_workspace_id())
    """,
    "ALTER TABLE embedding_profiles ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE embedding_profiles FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY p_embedding_profiles_tenant ON embedding_profiles
      USING (workspace_id = app_workspace_id()) WITH CHECK (workspace_id = app_workspace_id())
    """,
    "ALTER TABLE git_connections ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE git_connections FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY p_git_connections_tenant ON git_connections
      USING (workspace_id = app_workspace_id()) WITH CHECK (workspace_id = app_workspace_id())
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in [
        "git_connections",
        "embedding_profiles",
        "task_occurrences",
        "document_changes",
        "snapshot_documents",
        "source_snapshots",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for column in [
        "snapshot_id",
        "parent_job_id",
        "payload_json",
        "failed_items_json",
        "counters_json",
        "progress_json",
    ]:
        op.execute(f"ALTER TABLE job_runs DROP COLUMN IF EXISTS {column}")
    for column in ["line_start", "line_end"]:
        op.execute(f"ALTER TABLE document_chunks DROP COLUMN IF EXISTS {column}")
    for column in ["raw_hash", "parser_version", "tokenizer_version"]:
        op.execute(f"ALTER TABLE documents DROP COLUMN IF EXISTS {column}")
