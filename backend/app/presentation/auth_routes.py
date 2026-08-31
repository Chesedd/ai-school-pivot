"""Browser session authentication endpoints."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth_errors import InactiveAccountError, InvalidCredentialsError
from app.application.authentication import AuthenticationService
from app.application.principal import Principal, PrincipalResolver
from app.config import Settings, get_settings
from app.db.session import get_session
from app.presentation.auth_dependencies import (
    get_authentication_service,
    get_principal_resolver,
    require_principal,
)
from app.security.origin import TrustedOriginPolicy

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: Annotated[str, Field(min_length=1, max_length=254)]
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class PrincipalResponse(BaseModel):
    user_id: UUID
    login: str
    display_name: str
    roles: list[str]
    capabilities: list[str]
    student_id: UUID | None

    @classmethod
    def from_principal(cls, principal: Principal) -> "PrincipalResponse":
        return cls(
            user_id=principal.user_id,
            login=principal.login,
            display_name=principal.display_name,
            roles=sorted(principal.roles),
            capabilities=sorted(principal.capabilities),
            student_id=principal.student_id,
        )


def require_trusted_origin(request: Request, settings: Settings = Depends(get_settings)) -> None:
    TrustedOriginPolicy(settings.allowed_origins).enforce(request)


@router.post("/login", response_model=PrincipalResponse, dependencies=[Depends(require_trusted_origin)])
async def login(
    credentials: LoginRequest,
    response: Response,
    service: AuthenticationService = Depends(get_authentication_service),
    resolver: PrincipalResolver = Depends(get_principal_resolver),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PrincipalResponse:
    try:
        issuance = await service.login(credentials.login, credentials.password)
    except (InvalidCredentialsError, InactiveAccountError) as exc:
        raise HTTPException(status_code=401, detail="invalid_credentials") from exc
    principal = await resolver.for_account(issuance.user)
    await session.commit()
    max_age = max(0, int((issuance.expires_at - datetime.now(timezone.utc)).total_seconds()))
    response.set_cookie(
        settings.auth_session_cookie_name,
        issuance.session_secret,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_session_cookie_secure,
        samesite=settings.auth_session_cookie_samesite,
        path="/",
    )
    return PrincipalResponse.from_principal(principal)


@router.get("/me", response_model=PrincipalResponse)
async def me(principal: Principal = Depends(require_principal)) -> PrincipalResponse:
    return PrincipalResponse.from_principal(principal)


@router.post("/logout", status_code=204, dependencies=[Depends(require_trusted_origin)])
async def logout(
    request: Request,
    response: Response,
    service: AuthenticationService = Depends(get_authentication_service),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    secret = request.cookies.get(settings.auth_session_cookie_name)
    if secret:
        await service.logout(secret)
        await session.commit()
    response.delete_cookie(
        settings.auth_session_cookie_name,
        path="/",
        secure=settings.auth_session_cookie_secure,
        httponly=True,
        samesite=settings.auth_session_cookie_samesite,
    )
