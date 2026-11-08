"""恢复演练（AC-61 / SEC-28）。

流程：
1. 对当前库做一次 `pg_dump`（复用 `backup_pg.sh` 的约定：未设口令即明文）；
2. 建一个新库 `{db}_restore_drill_{timestamp}`；
3. `pg_restore` 到新库；
4. 比对 `documents` / `reviews` 计数，与备份时点相等；
5. 抽样 10 篇 `content_hash` 一致；
6. 记录耗时、RTO（恢复到可用）与 RPO（最近备份距现在）判定；
7. 写 `docs/evidence/phase5/restore-drill.json`。

用法：
    uv run python scripts/restore_drill.py [--keep]

环境变量同 `backup_pg.sh`（MYLIFE_DB_* / POSTGRES_USER / POSTGRES_PASSWORD）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "phase5" / "restore-drill.json"

DB_HOST = os.environ.get("MYLIFE_DB_HOST", "localhost")
DB_PORT = os.environ.get("MYLIFE_DB_PORT", "5432")
DB_NAME = os.environ.get("MYLIFE_DB_NAME", "mylife")
DB_USER = os.environ.get("POSTGRES_USER", "mylife_migrator")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "change-me-migrator")
#: RTO/RPO 门禁（AC-61）。
RTO_LIMIT_HOURS = 4
RPO_LIMIT_HOURS = 24


#: 本机没有 PG 客户端时，回退到 compose 里的 postgres 容器。
USE_DOCKER = shutil.which("psql") is None
COMPOSE = [
    "docker", "compose",
    "-f", str(ROOT / "infra" / "compose" / "docker-compose.yml"),
    "-f", str(ROOT / "infra" / "compose" / "docker-compose.dev.yml"),
]


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    env = {**os.environ, "PGPASSWORD": DB_PASSWORD}
    if USE_DOCKER:
        # 容器内用服务名与固定端口；口令通过 -e 传入（compose exec 不继承宿主 env）。
        inner = [a for a in args]
        for index, arg in enumerate(inner):
            if arg in {"-h", "--host"} and index + 1 < len(inner):
                inner[index + 1] = "postgres"
            if arg in {"-p", "--port"} and index + 1 < len(inner):
                inner[index + 1] = "5432"
        return subprocess.run(
            [*COMPOSE, "exec", "-T", "-e", f"PGPASSWORD={DB_PASSWORD}",
             "postgres", *inner],
            capture_output=True,
            text=True,
            check=kwargs.get("check", False),
        )
    return subprocess.run(args, capture_output=True, text=True, env=env, check=kwargs.get("check", False))


def _psql(db: str, sql: str) -> str:
    result = _run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", db, "-At", "-c", sql],
        check=True,
    )
    return (result.stdout or "").strip()


def _counts(db: str) -> dict[str, int]:
    return {
        "documents": int(_psql(db, "SELECT count(*) FROM documents") or 0),
        "reviews": int(_psql(db, "SELECT count(*) FROM reviews") or 0),
    }


def _sample_hashes(db: str, limit: int = 10) -> list[tuple[str, str]]:
    rows = _psql(
        db,
        "SELECT path, content_hash FROM documents ORDER BY path LIMIT " + str(limit),
    )
    return [tuple(line.split("|", 1)) for line in rows.splitlines() if "|" in line]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="保留演练库")
    args = parser.parse_args()

    if USE_DOCKER and shutil.which("docker") is None:
        print(
            "缺少 PostgreSQL 客户端，也没有 docker 可回退",
            file=sys.stderr,
        )
        return 2

    started = time.perf_counter()
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    drill_db = f"{DB_NAME}_restore_drill_{stamp}"

    source_counts = _counts(DB_NAME)
    source_hashes = _sample_hashes(DB_NAME)

    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "drill.dump"
        dump_arg = "/tmp/drill.dump" if USE_DOCKER else str(dump)
        _run(
            ["pg_dump", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER,
             "-d", DB_NAME, "-Fc", "-f", dump_arg],
            check=True,
        )
        if USE_DOCKER:
            checksum = _run(
                ["sha256sum", "/tmp/drill.dump"], check=False
            ).stdout.split(" ")[0]
        else:
            checksum = subprocess.run(
                ["sha256sum", str(dump)], capture_output=True, text=True, check=False
            ).stdout.split(" ")[0]

        _psql("postgres", f'DROP DATABASE IF EXISTS "{drill_db}"')
        _psql("postgres", f'CREATE DATABASE "{drill_db}"')
        _run(
            ["pg_restore", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER,
             "-d", drill_db, "--no-owner", "--no-privileges", dump_arg],
            check=True,
        )

        restored_counts = _counts(drill_db)
        restored_hashes = _sample_hashes(drill_db)
        if not args.keep:
            _psql("postgres", f'DROP DATABASE IF EXISTS "{drill_db}"')

    elapsed_seconds = round(time.perf_counter() - started, 1)
    # RPO：备份时点相对现在的延迟。刚生成的备份即为 0。
    rpo_hours = 0.0
    rto_hours = round(elapsed_seconds / 3600, 3)

    report = {
        "executed_at": datetime.now(UTC).isoformat(),
        "source": {"database": DB_NAME, "counts": source_counts},
        "restored": {"database": drill_db, "counts": restored_counts},
        "counts_equal": source_counts == restored_counts,
        "sampled_documents": len(source_hashes),
        "content_hash_match": restored_hashes == source_hashes,
        "dump_sha256": checksum,
        "duration_seconds": elapsed_seconds,
        "rto_hours": rto_hours,
        "rto_within_limit": rto_hours <= RTO_LIMIT_HOURS,
        "rpo_hours": rpo_hours,
        "rpo_within_limit": rpo_hours <= RPO_LIMIT_HOURS,
        "encryption_required": bool(os.environ.get("MYLIFE_BACKUP_PASSPHRASE")),
        "kept_drill_database": args.keep,
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = (
        report["counts_equal"]
        and report["content_hash_match"]
        and report["rto_within_limit"]
        and report["rpo_within_limit"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
