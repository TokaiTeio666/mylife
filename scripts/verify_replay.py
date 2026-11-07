"""AC-60 回放校验：用 run_events 从 CREATED 重放到终态，与 DB 状态比对。

用法：
    uv run python scripts/verify_replay.py <run_id> [--workspace <uuid>]

校验：
1. 首条 run.state 的 from_state 为空或 created；
2. 每条 run.state 的 from_state == 上一条 to_state（链式一致）；
3. 最后一条 to_state == agent_runs.state；
4. 回放在只读事务中执行，不产生任何写操作。
退出码 0 = 一致；1 = 不一致或 run 不存在。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import psycopg

TERMINAL = {"succeeded", "failed", "cancelled", "needs_human", "security_abort"}


def _dsn() -> str:
    url = os.environ.get(
        "MYLIFE_MIGRATION_URL",
        "postgresql://mylife_migrator:change-me-migrator@localhost:25432/mylife",
    )
    return url.replace("+psycopg", "")


def replay(conn: psycopg.Connection, run_id: str, workspace_id: str | None) -> dict[str, Any]:
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        params: list[Any] = [run_id]
        where = "run_id = %s"
        if workspace_id:
            where += " AND workspace_id = %s"
            params.append(workspace_id)
        rows = conn.execute(
            "SELECT event_id, event, from_state, to_state, payload, stream_epoch "
            f"FROM run_events WHERE {where} ORDER BY event_id",
            params,
        ).fetchall()
        run = conn.execute(
            "SELECT state, attempt_id FROM agent_runs WHERE id = %s", (run_id,)
        ).fetchone()

    transitions = [
        (row[2], row[3], row[4]) for row in rows if row[1] == "run.state"
    ]
    errors: list[str] = []
    if run is None:
        errors.append("run 不存在")
    if not transitions:
        errors.append("没有 run.state 事件")
    else:
        first_from = transitions[0][0]
        if first_from not in (None, "", "created"):
            errors.append(f"首条 from_state 非法: {first_from}")
        for index in range(1, len(transitions)):
            previous_to = transitions[index - 1][1]
            current_from = transitions[index][0]
            if current_from != previous_to:
                errors.append(
                    f"第 {index} 条断链: {current_from} != {previous_to}"
                )
        if run is not None and transitions[-1][1] != run[0]:
            errors.append(
                f"末条 to_state {transitions[-1][1]} != DB state {run[0]}"
            )

    return {
        "run_id": run_id,
        "events": len(rows),
        "transitions": len(transitions),
        "final_state": run[0] if run else None,
        "sequence": [row[1] for row in transitions],
        "errors": errors,
        "ok": not errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 run_events 可回放性（AC-60）")
    parser.add_argument("run_id")
    parser.add_argument("--workspace", default=None)
    args = parser.parse_args(argv)

    with psycopg.connect(_dsn()) as conn:
        result = replay(conn, args.run_id, args.workspace)

    print(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
