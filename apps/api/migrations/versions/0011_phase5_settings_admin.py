"""Phase 5：管理后台、LLM/Embedding 设置、导出与蓝绿换维的数据模型

Revision ID: 0011_phase5_settings_admin
Revises: 0010_phase4_topic_backfill
Create Date: 2026-11-08

注意：`alembic_version.version_num` 是 varchar(32)，revision 串不得超过 32 字符，
因此文件名与 revision 串不同（同 0006/0007 的处理）。

对齐：ACCEPTANCE-CRITERIA AC-07/49/50/53/54/55/61、SECURITY-BOUNDARY SEC-28/29/30、
API-CONTRACTS §3.9–§3.11、DATA-MODEL §4.22–§4.26/§7/§9、PHASE5-SPEC §2。

与文档的偏差（登记 docs/IMPLEMENTATION-NOTES.md §2E）：
- `platform_settings`：API §3.10 需要逐键 `{key,value,locked,source}`，但现有
  只有一个全局布尔；新增非租户表，环境变量锁定的键不落库（PHASE5-SPEC D5）。
- `users.last_login_at/disabled_reason/disabled_at`：`AdminUser` 已声明
  `last_login_at`，禁用原因在 §3.10 请求体中出现，但表无落点（D4/AC-54）。
- `provider_credentials.base_url/model/model_profile` 与 `status` 追加
  `untested`：LLM 连接参数此前只在环境变量里，而 API 要求可读写（D6/D7）。
- `embedding_profiles.chunk_table/coverage/previous_profile_id/activated_at/
  retire_at`：蓝绿换维需要显式的新表名、覆盖率与回滚指针（D8/D9/D30）。
- `export_jobs.scope/include_json/format/progress_json/artifact_bytes/
  updated_at` 与 `status` 追加 `ready`：API §3.11 的响应字段与 DB 现状不符
  （D3）。
- `embedding_reembed_jobs`：全量 re-embed 的进度需要可查询（AC-50）。
- `admin_*` 系列 SECURITY DEFINER 函数（owner=`mylife_admin`，BYPASSRLS）：
  FORCE RLS 下管理端跨租户只读恒 0 行，且只允许暴露元数据列（D4/SEC-30）。
- `dispatch_due_exports()` / `dispatch_stale_ingest_jobs()`：导出过期清理与
  僵死 `running` 回收需要跨租户扫描（D25/§6.4）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_phase5_settings_admin"
down_revision: str | None = "0010_phase4_topic_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_TENANT_TABLES = ["embedding_reembed_jobs"]

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
    # ------------------------------------------------------------ users
    "ALTER TABLE users ADD COLUMN last_login_at timestamptz",
    "ALTER TABLE users ADD COLUMN disabled_reason text",
    "ALTER TABLE users ADD COLUMN disabled_at timestamptz",
    "CREATE INDEX idx_users_created ON users (created_at DESC)",
    # ------------------------------------------------------------ provider_credentials
    "ALTER TABLE provider_credentials ADD COLUMN base_url text",
    "ALTER TABLE provider_credentials ADD COLUMN model text",
    "ALTER TABLE provider_credentials ADD COLUMN model_profile text",
    # `provider` 列区分凭据用途（llm/embedding），`provider_name` 才是厂商
    # （deepseek/openai_compatible/mock）；两者语义不同，不能复用同一列。
    "ALTER TABLE provider_credentials ADD COLUMN provider_name text",
    "ALTER TABLE provider_credentials DROP CONSTRAINT "
    "provider_credentials_status_check",
    "ALTER TABLE provider_credentials ADD CONSTRAINT "
    "provider_credentials_status_check "
    "CHECK (status IN ('active','invalid','disabled','untested'))",
    # ------------------------------------------------------------ embedding_profiles
    "ALTER TABLE embedding_profiles ADD COLUMN chunk_table text",
    "ALTER TABLE embedding_profiles ADD COLUMN coverage numeric(5,4)",
    "ALTER TABLE embedding_profiles ADD COLUMN previous_profile_id uuid "
    "REFERENCES embedding_profiles(id) ON DELETE SET NULL",
    "ALTER TABLE embedding_profiles ADD COLUMN activated_at timestamptz",
    "ALTER TABLE embedding_profiles ADD COLUMN retire_at timestamptz",
    "ALTER TABLE embedding_profiles ADD COLUMN updated_at timestamptz "
    "NOT NULL DEFAULT now()",
    # 历史 1024 维指向基表，其余维度按 `document_chunks_v{dim}` 回填，保证降级再升级幂等。
    "UPDATE embedding_profiles SET chunk_table = CASE WHEN dimension = 1024 "
    "THEN 'document_chunks' ELSE 'document_chunks_v' || dimension::text END "
    "WHERE chunk_table IS NULL",
    "ALTER TABLE embedding_profiles ALTER COLUMN chunk_table SET NOT NULL",
    "CREATE UNIQUE INDEX uq_embedding_chunk_table "
    "ON embedding_profiles (workspace_id, chunk_table)",
    "CREATE INDEX idx_embedding_retire ON embedding_profiles (retire_at) "
    "WHERE status = 'retired'",
    # workspace_settings.embedding_profile_id 此前无外键。
    "ALTER TABLE workspace_settings ADD CONSTRAINT "
    "workspace_settings_embedding_profile_fk "
    "FOREIGN KEY (embedding_profile_id) REFERENCES embedding_profiles(id) "
    "ON DELETE SET NULL",
    # ------------------------------------------------------------ embedding_reembed_jobs
    """
    CREATE TABLE embedding_reembed_jobs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      profile_id uuid NOT NULL REFERENCES embedding_profiles(id) ON DELETE CASCADE,
      status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','succeeded','failed')),
      processed_chunks bigint NOT NULL DEFAULT 0,
      total_chunks bigint NOT NULL DEFAULT 0,
      error text,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz
    )
    """,
    "CREATE INDEX idx_reembed_ws_status "
    "ON embedding_reembed_jobs (workspace_id, status)",
    "CREATE UNIQUE INDEX uq_reembed_active ON embedding_reembed_jobs "
    "(workspace_id) WHERE status IN ('queued','running')",
    # ------------------------------------------------------------ export_jobs
    "ALTER TABLE export_jobs ADD COLUMN scope text NOT NULL DEFAULT 'workspace'",
    "ALTER TABLE export_jobs ADD CONSTRAINT export_jobs_scope_check "
    "CHECK (scope IN ('workspace','account'))",
    "ALTER TABLE export_jobs ADD COLUMN include_json jsonb NOT NULL "
    "DEFAULT '[]'::jsonb",
    "ALTER TABLE export_jobs ADD COLUMN format text NOT NULL DEFAULT 'zip'",
    "ALTER TABLE export_jobs ADD COLUMN progress_json jsonb NOT NULL "
    "DEFAULT '{}'::jsonb",
    "ALTER TABLE export_jobs ADD COLUMN artifact_bytes bigint",
    "ALTER TABLE export_jobs ADD COLUMN updated_at timestamptz "
    "NOT NULL DEFAULT now()",
    "ALTER TABLE export_jobs DROP CONSTRAINT export_jobs_status_check",
    "ALTER TABLE export_jobs ADD CONSTRAINT export_jobs_status_check "
    "CHECK (status IN ('queued','running','ready','succeeded','failed','expired'))",
    "CREATE INDEX idx_export_ws_created "
    "ON export_jobs (workspace_id, created_at DESC)",
    "CREATE UNIQUE INDEX uq_export_active ON export_jobs (workspace_id, scope) "
    "WHERE status IN ('queued','running')",
    # ------------------------------------------------------------ platform_settings
    """
    CREATE TABLE platform_settings (
      key text PRIMARY KEY,
      value_json jsonb NOT NULL,
      updated_at timestamptz NOT NULL DEFAULT now(),
      updated_by uuid REFERENCES users(id) ON DELETE SET NULL
    )
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS + RLS_STATEMENTS:
        op.execute(statement)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON platform_settings TO mylife_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON embedding_reembed_jobs TO mylife_app")
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mylife_admin') THEN
            CREATE ROLE mylife_admin NOLOGIN BYPASSRLS;
          END IF;
        END $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO mylife_admin")
    op.execute(
        "GRANT SELECT ON users, workspaces, workspace_members, audit_logs, "
        "usage_events, reviews, documents, deletion_requests TO mylife_admin"
    )
    # 管理端跨租户只读：只暴露元数据列，绝不返回正文（SEC-30）。
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_list_users(
          p_limit int, p_offset int, p_q text DEFAULT NULL, p_status text DEFAULT NULL
        )
        RETURNS TABLE (
          id uuid, email text, display_name text, status text,
          created_at timestamptz, last_login_at timestamptz,
          workspace_count bigint
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT u.id, u.email, u.display_name, u.status, u.created_at, u.last_login_at,
                 (SELECT count(*) FROM workspace_members wm WHERE wm.user_id = u.id)
          FROM users u
          WHERE (p_q IS NULL OR u.email ILIKE '%' || p_q || '%'
                 OR u.display_name ILIKE '%' || p_q || '%')
            AND (p_status IS NULL OR u.status = p_status)
          ORDER BY u.created_at DESC
          LIMIT p_limit OFFSET p_offset
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_count_users(
          p_q text DEFAULT NULL, p_status text DEFAULT NULL
        )
        RETURNS bigint
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT count(*) FROM users u
          WHERE (p_q IS NULL OR u.email ILIKE '%' || p_q || '%'
                 OR u.display_name ILIKE '%' || p_q || '%')
            AND (p_status IS NULL OR u.status = p_status)
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_list_workspaces(
          p_limit int, p_offset int, p_q text DEFAULT NULL
        )
        RETURNS TABLE (
          id uuid, name text, slug text, owner_id uuid, plan text,
          created_at timestamptz, documents bigint, reviews bigint,
          tokens_month bigint, deletion_state text
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT w.id, w.name, w.slug, w.owner_id, w.plan, w.created_at,
                 (SELECT count(*) FROM documents d WHERE d.workspace_id = w.id),
                 (SELECT count(*) FROM reviews r WHERE r.workspace_id = w.id),
                 COALESCE((
                   SELECT sum(ue.prompt_tokens + ue.completion_tokens)
                   FROM usage_events ue
                   WHERE ue.workspace_id = w.id
                     AND ue.created_at >= date_trunc('month', now())
                 ), 0),
                 (SELECT dr.state FROM deletion_requests dr
                  WHERE dr.workspace_id = w.id
                  ORDER BY dr.created_at DESC LIMIT 1)
          FROM workspaces w
          WHERE (p_q IS NULL OR w.name ILIKE '%' || p_q || '%'
                 OR w.slug ILIKE '%' || p_q || '%')
          ORDER BY w.created_at DESC
          LIMIT p_limit OFFSET p_offset
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_count_workspaces(p_q text DEFAULT NULL)
        RETURNS bigint
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT count(*) FROM workspaces w
          WHERE (p_q IS NULL OR w.name ILIKE '%' || p_q || '%'
                 OR w.slug ILIKE '%' || p_q || '%')
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_get_workspace(p_id uuid)
        RETURNS TABLE (
          id uuid, name text, slug text, owner_id uuid, plan text,
          created_at timestamptz, documents bigint, reviews bigint,
          tokens_month bigint, deletion_state text
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT w.id, w.name, w.slug, w.owner_id, w.plan, w.created_at,
                 (SELECT count(*) FROM documents d WHERE d.workspace_id = w.id),
                 (SELECT count(*) FROM reviews r WHERE r.workspace_id = w.id),
                 COALESCE((
                   SELECT sum(ue.prompt_tokens + ue.completion_tokens)
                   FROM usage_events ue
                   WHERE ue.workspace_id = w.id
                     AND ue.created_at >= date_trunc('month', now())
                 ), 0),
                 (SELECT dr.state FROM deletion_requests dr
                  WHERE dr.workspace_id = w.id
                  ORDER BY dr.created_at DESC LIMIT 1)
          FROM workspaces w WHERE w.id = p_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_list_audit_logs(
          p_limit int, p_offset int, p_action text DEFAULT NULL,
          p_workspace uuid DEFAULT NULL,
          p_from timestamptz DEFAULT NULL, p_to timestamptz DEFAULT NULL
        )
        RETURNS TABLE (
          id uuid, workspace_id uuid, user_id uuid, action text, target text,
          detail jsonb, ip inet, created_at timestamptz
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT a.id, a.workspace_id, a.user_id, a.action, a.target, a.detail,
                 a.ip, a.created_at
          FROM audit_logs a
          WHERE (p_action IS NULL OR a.action = p_action)
            AND (p_workspace IS NULL OR a.workspace_id = p_workspace)
            AND (p_from IS NULL OR a.created_at >= p_from)
            AND (p_to IS NULL OR a.created_at < p_to)
          ORDER BY a.created_at DESC
          LIMIT p_limit OFFSET p_offset
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_count_audit_logs(
          p_action text DEFAULT NULL, p_workspace uuid DEFAULT NULL,
          p_from timestamptz DEFAULT NULL, p_to timestamptz DEFAULT NULL
        )
        RETURNS bigint
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT count(*) FROM audit_logs a
          WHERE (p_action IS NULL OR a.action = p_action)
            AND (p_workspace IS NULL OR a.workspace_id = p_workspace)
            AND (p_from IS NULL OR a.created_at >= p_from)
            AND (p_to IS NULL OR a.created_at < p_to)
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_usage_totals(
          p_from timestamptz, p_to timestamptz
        )
        RETURNS TABLE (
          workspace_id uuid, prompt_tokens bigint, completion_tokens bigint,
          calls bigint, cost numeric
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT ue.workspace_id,
                 COALESCE(sum(ue.prompt_tokens), 0),
                 COALESCE(sum(ue.completion_tokens), 0),
                 count(*),
                 COALESCE(sum(ue.cost), 0)
          FROM usage_events ue
          WHERE ue.created_at >= p_from AND ue.created_at < p_to
          GROUP BY ue.workspace_id
          ORDER BY ue.workspace_id
        $$
        """
    )
    for signature in (
        "admin_list_users(int,int,text,text)",
        "admin_count_users(text,text)",
        "admin_list_workspaces(int,int,text)",
        "admin_count_workspaces(text)",
        "admin_get_workspace(uuid)",
        "admin_list_audit_logs(int,int,text,uuid,timestamptz,timestamptz)",
        "admin_count_audit_logs(text,uuid,timestamptz,timestamptz)",
        "admin_usage_totals(timestamptz,timestamptz)",
    ):
        op.execute(f"ALTER FUNCTION {signature} OWNER TO mylife_admin")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO mylife_app")
    # ------------------------------------------------------------ 蓝绿换维 DDL
    # `mylife_app` 在 public 上没有 CREATE 权限（最小权限），因此换维的新表
    # 由 `mylife_migrator` 持有的 SECURITY DEFINER 函数创建；函数内部校验
    # 表名与维度，并显式补 RLS（`LIKE ... INCLUDING ALL` 不复制策略）。
    op.execute(
        """
        CREATE OR REPLACE FUNCTION create_chunk_table(p_table text, p_dim int)
        RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        BEGIN
          IF p_table !~ '^document_chunks_v[0-9]{1,3}$' THEN
            RAISE EXCEPTION 'invalid chunk table name: %', p_table;
          END IF;
          IF p_dim < 256 OR p_dim > 3072 THEN
            RAISE EXCEPTION 'invalid embedding dimension: %', p_dim;
          END IF;
          IF to_regclass(p_table) IS NOT NULL THEN
            RAISE EXCEPTION 'chunk table already exists: %', p_table;
          END IF;
          EXECUTE format(
            'CREATE TABLE %I ('
            '  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),'
            '  workspace_id uuid NOT NULL,'
            '  document_id uuid NOT NULL,'
            '  heading text,'
            '  content text NOT NULL,'
            '  token_count int NOT NULL,'
            '  chunk_ordinal int NOT NULL,'
            '  chunk_hash text NOT NULL,'
            '  embedding vector(%s),'
            '  embedding_profile_id uuid,'
            '  line_start int,'
            '  line_end int,'
            '  UNIQUE (document_id, chunk_ordinal),'
            '  FOREIGN KEY (workspace_id, document_id)'
            '    REFERENCES documents (workspace_id, id) ON DELETE CASCADE'
            ')', p_table, p_dim
          );
          EXECUTE format(
            'CREATE INDEX %I ON %I USING hnsw (embedding vector_cosine_ops) '
            'WITH (m = 16, ef_construction = 64)',
            p_table || '_embedding_idx', p_table
          );
          EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', p_table);
          EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', p_table);
          EXECUTE format(
            'CREATE POLICY p_%s_tenant ON %I USING (workspace_id = app_workspace_id()) '
            'WITH CHECK (workspace_id = app_workspace_id())',
            p_table, p_table
          );
          EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO mylife_app', p_table
          );
        END $$;
        """
    )
    op.execute(
        "ALTER FUNCTION create_chunk_table(text,int) OWNER TO mylife_migrator"
    )
    op.execute("REVOKE ALL ON FUNCTION create_chunk_table(text,int) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION create_chunk_table(text,int) TO mylife_app")
    # ------------------------------------------------------------ dispatcher 扫描
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dispatch_due_exports(p_limit int)
        RETURNS TABLE (id uuid, workspace_id uuid, artifact_key text)
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT ej.id, ej.workspace_id, ej.artifact_key
          FROM export_jobs ej
          WHERE ej.status IN ('queued','ready','succeeded')
            AND ej.expires_at IS NOT NULL AND ej.expires_at <= now()
          ORDER BY ej.expires_at ASC
          LIMIT p_limit
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dispatch_stale_ingest_jobs(p_limit int)
        RETURNS TABLE (id uuid, workspace_id uuid)
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT jr.id, jr.workspace_id
          FROM job_runs jr
          WHERE jr.status = 'running'
            AND jr.started_at IS NOT NULL
            AND jr.started_at < now() - interval '30 minutes'
          ORDER BY jr.started_at ASC
          LIMIT p_limit
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dispatch_retired_embedding_profiles(p_limit int)
        RETURNS TABLE (id uuid, workspace_id uuid, chunk_table text)
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT ep.id, ep.workspace_id, ep.chunk_table
          FROM embedding_profiles ep
          WHERE ep.status = 'retired' AND ep.retire_at IS NOT NULL
            AND ep.retire_at <= now()
          ORDER BY ep.retire_at ASC
          LIMIT p_limit
        $$
        """
    )
    for signature in (
        "dispatch_due_exports(int)",
        "dispatch_stale_ingest_jobs(int)",
        "dispatch_retired_embedding_profiles(int)",
    ):
        op.execute(f"ALTER FUNCTION {signature} OWNER TO mylife_dispatch")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO mylife_app")
    op.execute("GRANT SELECT ON export_jobs, job_runs, embedding_profiles TO mylife_dispatch")


def downgrade() -> None:
    # 函数可能不存在（例如从半应用状态回退），存在时才撤销并删除。
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = 'create_chunk_table'
          ) THEN
            EXECUTE 'REVOKE ALL ON FUNCTION create_chunk_table(text,int) FROM PUBLIC';
            EXECUTE 'DROP FUNCTION create_chunk_table(text,int)';
          END IF;
        END $$;
        """
    )
    for signature in (
        "dispatch_retired_embedding_profiles(int)",
        "dispatch_stale_ingest_jobs(int)",
        "dispatch_due_exports(int)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    for signature in (
        "admin_usage_totals(timestamptz,timestamptz)",
        "admin_count_audit_logs(text,uuid,timestamptz,timestamptz)",
        "admin_list_audit_logs(int,int,text,uuid,timestamptz,timestamptz)",
        "admin_get_workspace(uuid)",
        "admin_count_workspaces(text)",
        "admin_list_workspaces(int,int,text)",
        "admin_count_users(text,text)",
        "admin_list_users(int,int,text,text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TABLE IF EXISTS platform_settings CASCADE")
    op.execute("DROP INDEX IF EXISTS uq_export_active")
    op.execute("DROP INDEX IF EXISTS idx_export_ws_created")
    op.execute("ALTER TABLE export_jobs DROP CONSTRAINT IF EXISTS export_jobs_status_check")
    op.execute(
        "ALTER TABLE export_jobs ADD CONSTRAINT export_jobs_status_check "
        "CHECK (status IN ('queued','running','succeeded','failed','expired'))"
    )
    for column in (
        "updated_at",
        "artifact_bytes",
        "progress_json",
        "format",
        "include_json",
    ):
        op.execute(f"ALTER TABLE export_jobs DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE export_jobs DROP CONSTRAINT IF EXISTS export_jobs_scope_check")
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS scope")
    op.execute("DROP TABLE IF EXISTS embedding_reembed_jobs CASCADE")
    op.execute(
        "ALTER TABLE workspace_settings DROP CONSTRAINT IF EXISTS "
        "workspace_settings_embedding_profile_fk"
    )
    op.execute("DROP INDEX IF EXISTS idx_embedding_retire")
    op.execute("DROP INDEX IF EXISTS uq_embedding_chunk_table")
    for column in (
        "updated_at",
        "retire_at",
        "activated_at",
        "previous_profile_id",
        "coverage",
        "chunk_table",
    ):
        op.execute(f"ALTER TABLE embedding_profiles DROP COLUMN IF EXISTS {column}")
    op.execute(
        "ALTER TABLE provider_credentials DROP CONSTRAINT IF EXISTS "
        "provider_credentials_status_check"
    )
    op.execute(
        "ALTER TABLE provider_credentials ADD CONSTRAINT "
        "provider_credentials_status_check "
        "CHECK (status IN ('active','invalid','disabled'))"
    )
    for column in ("provider_name", "model_profile", "model", "base_url"):
        op.execute(f"ALTER TABLE provider_credentials DROP COLUMN IF EXISTS {column}")
    op.execute("DROP INDEX IF EXISTS idx_users_created")
    for column in ("disabled_at", "disabled_reason", "last_login_at"):
        op.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {column}")
