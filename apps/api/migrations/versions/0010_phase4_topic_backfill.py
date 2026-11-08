"""Phase 4：回填 topic_items 计数（PHASE4-SPEC §2.4）

Revision ID: 0010_phase4_topic_backfill
Revises: 0009_phase4_outbox_scan
Create Date: 2026-11-08

Phase 3 的 `upsert_topic_item` 每次无条件 `occurrence_count + 1`，与
`ON CONFLICT DO NOTHING` 的 occurrence 插入不一致，历史行可能虚高。
0005 只加了列，未按规格回填；这里统一以 occurrence 行数为准，
并把 `last_seen` 对齐到最大 `source_date`。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_phase4_topic_backfill"
down_revision: str | None = "0009_phase4_outbox_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE topic_items t SET
          occurrence_count = (
            SELECT count(*) FROM topic_occurrences o
            WHERE o.workspace_id = t.workspace_id AND o.item_id = t.id
          ),
          last_seen = COALESCE(
            (SELECT max(o.source_date) FROM topic_occurrences o
             WHERE o.workspace_id = t.workspace_id AND o.item_id = t.id),
            t.last_seen
          )
        """
    )


def downgrade() -> None:
    # 回填是数据修正，无法也不应回退（旧值本就是错误计数）。
    pass
