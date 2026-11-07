"""auth 路由（API-CONTRACTS §3.1）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from mylife_contract.auth import (
    CsrfResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    RegisterRequest,
    RegisterResponse,
)
from mylife_contract.constants import COOKIE_CSRF, COOKIE_SESSION
from mylife_contract.errors import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api import repositories as repo
from mylife_api import services
from mylife_api.deps import AuthState, client_ip, get_db, require_session
from mylife_api.errors import AppError
from mylife_api.security import (
    clear_cookie_kwargs,
    csrf_cookie_kwargs,
    hash_session_token,
    new_csrf_token,
    session_cookie_kwargs,
)
from mylife_api.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])

_MAX_AGE = settings.session_ttl_days * 24 * 3600


def _set_auth_cookies(response: Response, *, token: str, csrf: str) -> None:
    response.set_cookie(value=token, **_session_cookie(_MAX_AGE))  # type: ignore[arg-type]
    response.set_cookie(value=csrf, **_csrf_cookie(_MAX_AGE))  # type: ignore[arg-type]


def _session_cookie(max_age: int) -> dict[str, object]:
    return session_cookie_kwargs(max_age=max_age)


def _csrf_cookie(max_age: int) -> dict[str, object]:
    return csrf_cookie_kwargs(max_age=max_age)


@router.get("/csrf", response_model=CsrfResponse)
async def get_csrf(response: Response) -> CsrfResponse:
    token = new_csrf_token()
    response.set_cookie(value=token, **_csrf_cookie(_MAX_AGE))  # type: ignore[arg-type]
    return CsrfResponse(csrf_token=token)


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    result, token, csrf = await services.register(
        db,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        invite_code=payload.invite_code,
        ip=client_ip(request),
    )
    _set_auth_cookies(response, token=token, csrf=csrf)
    return result


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    current_session_id = None
    existing = request.cookies.get(COOKIE_SESSION)
    if existing:
        row = await repo.get_session_by_token_hash(db, hash_session_token(existing))
        if row is not None:
            current_session_id = row["id"]

    result, token, csrf = await services.login(
        db,
        email=payload.email,
        password=payload.password,
        ip=client_ip(request),
        current_session_id=current_session_id,
    )
    _set_auth_cookies(response, token=token, csrf=csrf)
    return result


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    auth: AuthState = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await services.logout(
        db,
        session_id=auth.session_id,
        user_id=auth.user_id,
        workspace_id=auth.active_workspace_id,
    )
    response.set_cookie(**clear_cookie_kwargs(COOKIE_SESSION))  # type: ignore[arg-type]
    response.set_cookie(**clear_cookie_kwargs(COOKIE_CSRF))  # type: ignore[arg-type]
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    request: Request,
    response: Response,
    auth: AuthState = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await services.logout_all(
        db, user_id=auth.user_id, workspace_id=auth.active_workspace_id, ip=client_ip(request)
    )
    response.set_cookie(**clear_cookie_kwargs(COOKIE_SESSION))  # type: ignore[arg-type]
    response.set_cookie(**clear_cookie_kwargs(COOKIE_CSRF))  # type: ignore[arg-type]
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse)
async def me(
    auth: AuthState = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    if auth.active_workspace_id is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"reason": "NO_ACTIVE_WORKSPACE"})
    return await services.me(
        db, user_id=auth.user_id, active_workspace_id=auth.active_workspace_id
    )
