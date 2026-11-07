"""admin 路由（API-CONTRACTS §3.10）。Phase 0 仅邀请码；双重校验见 `require_platform_admin`。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from mylife_contract.admin import (
    InviteCode,
    InviteCodeCreated,
    InviteCodeCreateRequest,
    InviteCodeCreateResponse,
    InviteCodeListResponse,
)
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories as repo
from mylife_api import services
from mylife_api.deps import AuthState, get_db, require_platform_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/invite-codes",
    response_model=InviteCodeCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite_codes(
    payload: InviteCodeCreateRequest,
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteCodeCreateResponse:
    rows = await services.create_invite_codes(
        db,
        admin_id=admin.user_id,
        count=payload.count,
        expires_in_days=payload.expires_in_days,
        note=payload.note,
    )
    return InviteCodeCreateResponse(
        items=[InviteCodeCreated(code=r["code"], expires_at=r["expires_at"]) for r in rows]
    )


@router.get("/invite-codes", response_model=InviteCodeListResponse)
async def list_invite_codes(
    status_filter: str | None = Query(default=None, alias="status"),
    admin: AuthState = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteCodeListResponse:
    rows = await repo.list_invite_codes(db, status=status_filter)
    return InviteCodeListResponse(
        items=[
            InviteCode(
                id=r["id"],
                code=r["code"],
                created_by=r["created_by"],
                used_by=r["used_by"],
                expires_at=r["expires_at"],
                used_at=r["used_at"],
                note=r["note"],
                status=r["status"],
            )
            for r in rows
        ]
    )
