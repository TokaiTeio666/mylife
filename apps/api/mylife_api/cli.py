"""运维命令行（Phase 0 引导）。

用法：
  uv run python -m mylife_api.cli create-admin \\
      --email admin@example.com --password '...' --display-name 管理员
  uv run python -m mylife_api.cli create-invite --count 3 --days 30

说明：首个平台管理员无法通过 API 创建（公开注册是邀请制），因此提供受控 CLI。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from mylife_contract.constants import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from sqlalchemy import text

from mylife_api.db import dispose_engine, session_scope
from mylife_api.ids import invite_code as make_invite_code
from mylife_api.ids import uuid7
from mylife_api.security import hash_password


async def _create_admin(email: str, password: str, display_name: str) -> UUID:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise SystemExit(f"密码必须为 {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 个字符")
    email = email.strip().lower()
    user_id = uuid7()
    async with session_scope() as session:
        existing = await session.execute(
            text("SELECT id FROM users WHERE lower(email) = :email"), {"email": email}
        )
        if existing.first() is not None:
            raise SystemExit("该邮箱已存在")
        await session.execute(
            text(
                "INSERT INTO users (id, email, password_hash, display_name, is_platform_admin) "
                "VALUES (:id, :email, :hash, :name, true)"
            ),
            {
                "id": user_id,
                "email": email,
                "hash": hash_password(password),
                "name": display_name.strip(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO audit_logs (id, workspace_id, user_id, action, target, detail) "
                "VALUES (gen_random_uuid(), NULL, :uid, 'admin.bootstrap', :target, '{}'::jsonb)"
            ),
            {"uid": user_id, "target": email},
        )
    return user_id


async def _create_invites(count: int, days: int, admin_email: str | None) -> list[str]:
    async with session_scope() as session:
        admin = None
        if admin_email:
            row = await session.execute(
                text("SELECT id FROM users WHERE lower(email) = :email AND is_platform_admin"),
                {"email": admin_email.lower()},
            )
            admin = row.first()
        if admin is None:
            row = await session.execute(
                text(
                    "SELECT id FROM users WHERE is_platform_admin ORDER BY created_at LIMIT 1"
                )
            )
            admin = row.first()
        if admin is None:
            raise SystemExit("没有平台管理员；请先执行 create-admin")

        codes: list[str] = []
        for _ in range(count):
            code = make_invite_code()
            await session.execute(
                text(
                    "INSERT INTO invite_codes (id, code, created_by, expires_at) "
                    "VALUES (gen_random_uuid(), :code, :by, now() + make_interval(days => :days))"
                ),
                {"code": code, "by": admin[0], "days": days},
            )
            codes.append(code)
        return codes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mylife-api")
    sub = parser.add_subparsers(dest="command", required=True)

    admin_parser = sub.add_parser("create-admin", help="创建平台管理员（仅引导用）")
    admin_parser.add_argument("--email", required=True)
    admin_parser.add_argument("--password", required=True)
    admin_parser.add_argument("--display-name", default="平台管理员")

    invite_parser = sub.add_parser("create-invite", help="生成邀请码")
    invite_parser.add_argument("--count", type=int, default=1)
    invite_parser.add_argument("--days", type=int, default=30)
    invite_parser.add_argument("--admin-email")

    args = parser.parse_args(argv)

    async def run() -> None:
        try:
            if args.command == "create-admin":
                user_id = await _create_admin(args.email, args.password, args.display_name)
                print(f"已创建平台管理员: {args.email} ({user_id})")
            elif args.command == "create-invite":
                codes = await _create_invites(args.count, args.days, args.admin_email)
                for code in codes:
                    print(code)
        finally:
            await dispose_engine()

    if sys.platform == "win32":
        # psycopg 异步模式不支持 ProactorEventLoop。
        asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
