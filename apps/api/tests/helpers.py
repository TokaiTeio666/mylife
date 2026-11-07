"""测试辅助。"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from mylife_api.ids import uuid7
from mylife_api.security import hash_password

API = "/api/v1"


async def csrf(client) -> str:
    response = await client.get(f"{API}/auth/csrf")
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def csrf_headers(token: str) -> dict[str, str]:
    return {"X-CSRF-Token": token}


def current_csrf(client) -> str:
    token = client.cookies.get("mylife_csrf")
    assert token, "CSRF cookie 缺失"
    return token


def insert_invite(
    conn,
    code: str = "INV-TESTCODE1",
    *,
    expires_in_days: int | None = 30,
    used: bool = False,
    used_by: str | None = None,
) -> str:
    creator = insert_user(conn, email=f"admin-{uuid4().hex[:8]}@example.com")
    expires_at = (
        datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None
    )
    conn.execute(
        "INSERT INTO invite_codes (id, code, created_by, expires_at, used_by, used_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            uuid7(),
            code,
            creator,
            expires_at,
            used_by,
            datetime.now(UTC) if used else None,
        ),
    )
    return code


def insert_user(
    conn,
    *,
    email: str,
    password: str = "correct-horse-9",
    display_name: str = "测试用户",
    platform_admin: bool = False,
) -> str:
    user_id = uuid7()
    conn.execute(
        "INSERT INTO users (id, email, password_hash, display_name, is_platform_admin) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user_id, email, hash_password(password), display_name, platform_admin),
    )
    return str(user_id)


def insert_workspace(conn, *, owner_id: str, name: str = "测试库", slug: str | None = None) -> str:
    workspace_id = uuid7()
    conn.execute(
        "INSERT INTO workspaces (id, name, slug, owner_id) VALUES (%s, %s, %s, %s)",
        (workspace_id, name, slug or f"ws-{uuid4().hex[:8]}", owner_id),
    )
    conn.execute(
        "INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (%s, %s, 'owner')",
        (workspace_id, owner_id),
    )
    conn.execute(
        "INSERT INTO workspace_settings (workspace_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (workspace_id,),
    )
    conn.execute(
        "INSERT INTO workspace_schedules (workspace_id, kind, local_time, timezone) "
        "VALUES (%s, 'daily', '01:00', 'Asia/Shanghai') ON CONFLICT DO NOTHING",
        (workspace_id,),
    )
    return str(workspace_id)


def insert_document(
    conn,
    *,
    workspace_id: str,
    path: str,
    title: str | None = None,
    content: str = "内容",
    content_hash: str | None = None,
    size: int = 3,
) -> str:
    document_id = uuid7()
    conn.execute(
        "INSERT INTO documents "
        "(id, workspace_id, path, title, content, content_hash, size) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            document_id,
            workspace_id,
            path,
            title or path,
            content,
            content_hash or "a" * 64,
            size,
        ),
    )
    return str(document_id)


async def register(
    client,
    *,
    invite_code: str,
    email: str,
    password: str = "correct-horse-9",
    display_name: str = "测试用户",
):
    token = await csrf(client)
    return await client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": display_name,
            "invite_code": invite_code,
        },
        headers=csrf_headers(token),
    )


async def login(client, *, email: str, password: str = "correct-horse-9"):
    token = await csrf(client)
    return await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        headers=csrf_headers(token),
    )


def row_count(conn, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def make_zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, data)
    return buffer.getvalue()


async def upload_zip(client, *, data: bytes, filename: str = "vault.zip"):
    token = await csrf(client)
    return await client.post(
        f"{API}/ingest/zip",
        files={"file": (filename, data, "application/zip")},
        data={"mode": "managed", "reviews_root": "wiki/复盘"},
        headers=csrf_headers(token),
    )


async def run_ingest_job(job_id: str, workspace_id: str) -> None:
    from mylife_worker.jobs import run_job

    await run_job(UUID(job_id), UUID(workspace_id))


async def job_detail(client, job_id: str) -> dict[str, Any]:
    response = await client.get(f"{API}/ingest/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()
