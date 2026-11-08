"""Phase 5：蓝绿换维 4 位维度表名与旧 chunk 表退役函数

Revision ID: 0012_phase5_retire_chunk_table
Revises: 0011_phase5_settings_admin
Create Date: 2026-11-08

对齐：PHASE5-SPEC §4.3（第 9 条）、D8、D30、FX-11。
- 放宽 `create_chunk_table` 表名正则至 1–4 位数字（覆盖 1024..3072 维）；
- 新增 `retire_chunk_table(p_table text)`（`mylife_migrator` SECURITY DEFINER）：
  仅在无任何 workspace/profile 仍以 active/building 引用该表时执行 `DROP TABLE`，
  并返回退役前的行数与 chunk_hash 摘要供快照留档。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_phase5_retire_chunk_table"
down_revision: str | None = "0011_phase5_settings_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_chunk_table_sql(digits: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION create_chunk_table(p_table text, p_dim int)
    RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
    BEGIN
      IF p_table !~ '^document_chunks_v[0-9]{{{digits}}}$' THEN
        RAISE EXCEPTION 'invalid chunk table name: %', p_table;
      END IF;
      IF p_dim < 256 OR p_dim > 3072 THEN
        RAISE EXCEPTION 'invalid embedding dimension: %', p_dim;
      END IF;
      IF to_regclass(p_table) IS NOT NULL THEN
        RETURN;
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


def upgrade() -> None:
    op.execute(_create_chunk_table_sql("1,4"))
    op.execute("ALTER FUNCTION create_chunk_table(text,int) OWNER TO mylife_migrator")
    op.execute("REVOKE ALL ON FUNCTION create_chunk_table(text,int) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION create_chunk_table(text,int) TO mylife_app")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION retire_chunk_table(p_table text)
        RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
          v_rows bigint := 0;
          v_digest text := '';
        BEGIN
          IF p_table !~ '^document_chunks_v[0-9]{1,4}$' THEN
            RAISE EXCEPTION 'invalid chunk table name: %', p_table;
          END IF;
          IF EXISTS (
            SELECT 1 FROM embedding_profiles
            WHERE chunk_table = p_table AND status IN ('active', 'building')
          ) THEN
            RETURN jsonb_build_object('dropped', false, 'reason', 'still_active_or_building');
          END IF;
          IF EXISTS (
            SELECT 1 FROM workspace_settings s
            JOIN embedding_profiles p ON p.id = s.embedding_profile_id
            WHERE p.chunk_table = p_table
          ) THEN
            RETURN jsonb_build_object('dropped', false, 'reason', 'still_referenced');
          END IF;
          IF to_regclass(p_table) IS NULL THEN
            RETURN jsonb_build_object(
              'dropped', false, 'reason', 'already_absent', 'rows', 0, 'digest', ''
            );
          END IF;
          EXECUTE format(
            'SELECT count(*), '
            'COALESCE(md5(string_agg(chunk_hash, '','' ORDER BY id)), '''') '
            'FROM %I',
            p_table
          ) INTO v_rows, v_digest;
          EXECUTE format('DROP TABLE IF EXISTS %I', p_table);
          RETURN jsonb_build_object('dropped', true, 'rows', v_rows, 'digest', v_digest);
        END $$;
        """
    )
    op.execute("ALTER FUNCTION retire_chunk_table(text) OWNER TO mylife_migrator")
    op.execute("REVOKE ALL ON FUNCTION retire_chunk_table(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION retire_chunk_table(text) TO mylife_app")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = 'retire_chunk_table'
          ) THEN
            EXECUTE 'REVOKE ALL ON FUNCTION retire_chunk_table(text) FROM PUBLIC';
            EXECUTE 'DROP FUNCTION retire_chunk_table(text)';
          END IF;
        END $$;
        """
    )
    op.execute(_create_chunk_table_sql("1,3"))
    op.execute("ALTER FUNCTION create_chunk_table(text,int) OWNER TO mylife_migrator")
    op.execute("REVOKE ALL ON FUNCTION create_chunk_table(text,int) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION create_chunk_table(text,int) TO mylife_app")
