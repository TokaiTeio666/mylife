"""FX-14 导入性能基准（200MB ZIP / 5k Markdown，< 600s）。

用法：
  MYLIFE_DATABASE_URL=postgresql+psycopg://mylife_app:change-me-app@localhost:25432/mylife \
  uv run python scripts/bench_import.py

产出：docs/evidence/fx14-import.json
说明：为排除外部 API 延迟，本基准关闭 embedding（MYLIFE_EMBEDDING_PROVIDER=none），
      只测解包 + 解析 + 分块 + 入库（NFR-3 的“不含 LLM”口径）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ.setdefault("MYLIFE_EMBEDDING_PROVIDER", "none")
os.environ.setdefault("MYLIFE_QUEUE_BACKEND", "off")
os.environ.setdefault(
    "MYLIFE_DATABASE_URL",
    "postgresql+psycopg://mylife_app:change-me-app@localhost:25432/mylife",
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import psycopg  # noqa: E402

ADMIN_DSN = os.environ.get(
    "MYLIFE_BENCH_ADMIN_DSN",
    "postgresql://mylife_migrator:change-me-migrator@localhost:25432/mylife",
)
SLUG = "bench-import"


def build_zip(target: Path, docs: int, file_bytes: int) -> int:
    payload = os.urandom(file_bytes // 2).hex().encode("ascii")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for index in range(docs):
            header = f"---\ntitle: 笔记 {index}\ntags: [bench]\n---\n# 笔记 {index}\n".encode()
            archive.writestr(f"vault/notes/note-{index:05d}.md", header + payload)
    return target.stat().st_size


def prepare(admin: psycopg.Connection, zip_path: Path, zip_sha: str) -> tuple[str, str]:
    admin.execute("DELETE FROM workspaces WHERE slug = %s", (SLUG,))
    user_id = admin.execute(
        "INSERT INTO users (email, password_hash, display_name) "
        "VALUES ('bench-import@example.com', 'x', 'bench') "
        "ON CONFLICT (lower(email)) DO UPDATE SET updated_at = now() RETURNING id"
    ).fetchone()[0]
    workspace_id = admin.execute(
        "INSERT INTO workspaces (name, slug, owner_id) VALUES ('Bench Import', %s, %s) "
        "RETURNING id",
        (SLUG, user_id),
    ).fetchone()[0]
    job_id = admin.execute(
        "INSERT INTO job_runs (workspace_id, kind, status, payload_json) "
        "VALUES (%s, 'zip', 'queued', %s) RETURNING id",
        (
            workspace_id,
            json.dumps({"source": "zip", "zip_path": str(zip_path), "zip_sha256": zip_sha}),
        ),
    ).fetchone()[0]
    return str(workspace_id), str(job_id)


async def run(workspace_id: str, job_id: str) -> None:
    from uuid import UUID

    from mylife_worker.db import dispose_engine
    from mylife_worker.jobs import run_job

    await run_job(UUID(job_id), UUID(workspace_id))
    await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=5000)
    parser.add_argument("--file-bytes", type=int, default=40_000)
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="mylife-bench-import-"))
    os.environ["MYLIFE_DATA_ROOT"] = str(workdir / "data")
    zip_path = workdir / "vault.zip"

    print("building zip ...", flush=True)
    size = build_zip(zip_path, args.docs, args.file_bytes)

    import hashlib

    hasher = hashlib.sha256()
    with open(zip_path, "rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            hasher.update(block)
    digest = hasher.hexdigest()
    print(f"zip size: {size / 1024 / 1024:.1f} MB", flush=True)

    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        workspace_id, job_id = prepare(admin, zip_path, digest)

    started = time.perf_counter()
    asyncio.run(run(workspace_id, job_id))
    elapsed = time.perf_counter() - started

    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        status, counters, deadline = admin.execute(
            "SELECT status, counters_json, finished_at FROM job_runs WHERE id = %s",
            (job_id,),
        ).fetchone()
        documents = admin.execute(
            "SELECT count(*) FROM documents WHERE workspace_id = %s", (workspace_id,)
        ).fetchone()[0]
        chunks = admin.execute(
            "SELECT count(*) FROM document_chunks WHERE workspace_id = %s", (workspace_id,)
        ).fetchone()[0]

    report = {
        "zip_bytes": size,
        "documents_in_zip": args.docs,
        "documents_indexed": documents,
        "chunks": chunks,
        "job_status": status,
        "counters": counters,
        "elapsed_seconds": round(elapsed, 2),
        "under_600s": elapsed < 600,
        "embedding": "disabled",
    }
    output = Path("docs/evidence")
    output.mkdir(parents=True, exist_ok=True)
    (output / "fx14-import.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["under_600s"] and status in {"succeeded", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
