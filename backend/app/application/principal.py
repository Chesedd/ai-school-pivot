"""HTTP-independent authenticated principal resolution.

A student-role account without a persisted link remains authenticated with a
``None`` student_id.  A later student-domain boundary must reject that
unprovisioned state; this resolver never fabricates a development identity.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.application.capabilities import capabilities_for_roles


class Account(Protocol):
    user_id: UUID
    login: str
    display_name: str


class SessionAuthentication(Protocol):
    async def resolve_session(self, session_secret: str) -> Account: ...


class PrincipalRepository(Protocol):
    async def roles_for_user(self, user_id: UUID) -> frozenset[str]: ...
    async def link_for_user(self, user_id: UUID): ...


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    login: str
    display_name: str
    roles: frozenset[str]
    capabilities: frozenset[str]
    student_id: UUID | None


class PrincipalResolver:
    def __init__(self, authentication: SessionAuthentication, repository: PrincipalRepository):
        self.authentication = authentication
        self.repository = repository

    async def resolve(self, session_secret: str) -> Principal:
        account = await self.authentication.resolve_session(session_secret)
        return await self.for_account(account)

    async def for_account(self, account: Account) -> Principal:
        roles = await self.repository.roles_for_user(account.user_id)
        link = await self.repository.link_for_user(account.user_id)
        return Principal(
            user_id=account.user_id,
            login=account.login,
            display_name=account.display_name,
            roles=roles,
            capabilities=capabilities_for_roles(roles),
            student_id=None if link is None else link.student_id,
        )
