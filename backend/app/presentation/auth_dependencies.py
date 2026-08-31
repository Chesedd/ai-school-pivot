"""FastAPI composition for session authentication and coarse capabilities."""

from collections.abc import AsyncGenerator, Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth_errors import AuthenticationError
from app.application.authentication import AuthenticationService
from app.application.principal import Principal, PrincipalResolver
from app.config import Settings, get_settings
from app.db.session import get_session
from app.infrastructure.auth_repository import SQLAlchemyAuthRepository


def get_authentication_service(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)
) -> AuthenticationService:
    return AuthenticationService(SQLAlchemyAuthRepository(session), session_ttl=settings.auth_session_ttl)


def get_principal_resolver(
    service: AuthenticationService = Depends(get_authentication_service),
) -> PrincipalResolver:
    return PrincipalResolver(service, service.repository)


async def require_principal(
    request: Request,
    settings: Settings = Depends(get_settings),
    resolver: PrincipalResolver = Depends(get_principal_resolver),
) -> Principal:
    secret = request.cookies.get(settings.auth_session_cookie_name)
    if not secret:
        raise HTTPException(status_code=401, detail="authentication_required")
    try:
        return await resolver.resolve(secret)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="authentication_required") from exc


def require_student_identity(principal: Principal = Depends(require_principal)) -> UUID:
    """Return the current Assessment Student link or reject an unlinked account."""
    if principal.student_id is None:
        raise HTTPException(status_code=403, detail="student_identity_required")
    return principal.student_id


def require_capability(capability: str) -> Callable[..., AsyncGenerator[Principal, None]]:
    async def dependency(principal: Principal = Depends(require_principal)):
        if capability not in principal.capabilities:
            raise HTTPException(status_code=403, detail="forbidden")
        return principal

    return dependency
