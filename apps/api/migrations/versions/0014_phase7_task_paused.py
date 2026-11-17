"""Phase 7：Todo 增加「暂停中」状态

Revision ID: 0014_phase7_task_paused
Revises: 0013_phase6_monthly_review
Create Date: 2026-11-11

- `task_occurrences` 新增 `paused boolean NOT NULL DEFAULT false`；
- `tasks.status` 允许 `paused`（`- [-]` 解析结果）；
- 暂停任务不计入完成率分母，由查询侧过滤（见 `repo_chat.task_ledger_recalc`）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014_phase7_task_paused"
down_revision: str | None = "0013_phase6_monthly_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE task_occurrences "
        "ADD COLUMN IF NOT EXISTS paused boolean NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT tasks_status_check "
        "CHECK (status IN ('open','done','carried','dropped','paused'))"
    )


def downgrade() -> None:
    op.execute("UPDATE tasks SET status = 'open' WHERE status = 'paused'")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT tasks_status_check "
        "CHECK (status IN ('open','done','carried','dropped'))"
    )
    op.execute("ALTER TABLE task_occurrences DROP COLUMN IF EXISTS paused")
