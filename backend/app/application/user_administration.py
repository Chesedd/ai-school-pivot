"""Account administration orchestration, independent of HTTP."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from app.application.auth_errors import AccountAlreadyExistsError, InvalidAccountInputError
from app.application.authentication import AuthenticationService, normalize_login
from app.infrastructure.auth_repository import DuplicateNormalizedLogin, StudentLinkConflict
from app.security.passwords import InvalidPassword, PasswordHasher

VALID_ROLES = frozenset({"admin", "teacher", "student"})


class AdministrationError(Exception):
    def __init__(self, code: str, status: int):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class AdminUserView:
    user_id: UUID
    login: str
    display_name: str
    is_active: bool
    roles: tuple[str, ...]
    student_id: UUID | None
    created_at: datetime
    updated_at: datetime


class AdministrationRepository(Protocol):
    async def get_user(self, user_id: UUID): ...
    async def list_users(self, *, offset: int, limit: int): ...
    async def roles_for_user(self, user_id: UUID): ...
    async def link_for_user(self, user_id: UUID): ...
    async def link_for_student(self, student_id: UUID): ...
    async def student_exists(self, student_id: UUID) -> bool: ...
    async def create_student_link(self, user_id: UUID, student_id: UUID): ...
    async def remove_student_link(self, user_id: UUID) -> bool: ...
    async def replace_roles(self, user_id: UUID, roles: frozenset[str]): ...
    async def update_user_identity(self, user_id: UUID, *, login: str, normalized_login: str, display_name: str): ...
    async def set_user_active(self, user_id: UUID, active: bool): ...
    async def revoke_sessions_for_user(self, user_id: UUID, revoked_at: datetime): ...
    async def update_password_hash(self, user_id: UUID, password_hash: str): ...
    async def lock_admin_invariant(self): ...
    async def count_active_admins(self) -> int: ...


class UserAdministrationService:
    def __init__(self, repository: AdministrationRepository, authentication: AuthenticationService, *, password_hasher: PasswordHasher | None = None):
        self.repository, self.authentication = repository, authentication
        self.password_hasher = password_hasher or authentication.password_hasher

    async def _view(self, row) -> AdminUserView:
        roles = await self.repository.roles_for_user(row.id)
        link = await self.repository.link_for_user(row.id)
        return AdminUserView(row.id, row.login, row.display_name, row.is_active, tuple(sorted(roles)), None if link is None else link.student_id, row.created_at, row.updated_at)

    async def get(self, user_id: UUID) -> AdminUserView:
        row = await self.repository.get_user(user_id)
        if row is None: raise AdministrationError("user_not_found", 404)
        return await self._view(row)

    async def list(self, *, offset: int, limit: int) -> tuple[list[AdminUserView], int]:
        rows, total = await self.repository.list_users(offset=offset, limit=limit)
        return [await self._view(row) for row in rows], total

    def _roles(self, roles: set[str] | frozenset[str]) -> frozenset[str]:
        result = frozenset(roles)
        if not result <= VALID_ROLES: raise AdministrationError("invalid_role", 422)
        return result

    async def create(self, *, login: str, display_name: str, password: str, roles: set[str], student_id: UUID | None) -> AdminUserView:
        roles_value = self._roles(roles)
        if student_id is not None and "student" not in roles_value:
            raise AdministrationError("student_link_requires_student_role", 409)
        if student_id is not None and not await self.repository.student_exists(student_id):
            raise AdministrationError("student_not_found", 404)
        if student_id is not None and await self.repository.link_for_student(student_id):
            raise AdministrationError("student_link_conflict", 409)
        try:
            account = await self.authentication.create_account(login=login, display_name=display_name, password=password)
        except AccountAlreadyExistsError as exc: raise AdministrationError("account_already_exists", 409) from exc
        except InvalidAccountInputError as exc: raise AdministrationError("invalid_account_input", 422) from exc
        await self.repository.replace_roles(account.user_id, roles_value)
        if student_id is not None:
            try: await self.repository.create_student_link(account.user_id, student_id)
            except StudentLinkConflict as exc: raise AdministrationError("student_link_conflict", 409) from exc
        return await self.get(account.user_id)

    async def update(self, user_id: UUID, *, login: str | None, display_name: str | None, is_active: bool | None) -> AdminUserView:
        row = await self.repository.get_user(user_id)
        if row is None: raise AdministrationError("user_not_found", 404)
        try: visible, normalized = normalize_login(row.login if login is None else login)
        except InvalidAccountInputError as exc: raise AdministrationError("invalid_account_input", 422) from exc
        name = row.display_name if display_name is None else display_name.strip()
        if not name or len(name) > 200: raise AdministrationError("invalid_account_input", 422)
        try: await self.repository.update_user_identity(user_id, login=visible, normalized_login=normalized, display_name=name)
        except DuplicateNormalizedLogin as exc: raise AdministrationError("account_already_exists", 409) from exc
        if is_active is not None and is_active != row.is_active:
            if not is_active:
                roles = await self.repository.roles_for_user(user_id)
                if "admin" in roles:
                    await self.repository.lock_admin_invariant()
                    if await self.repository.count_active_admins() <= 1: raise AdministrationError("last_active_admin", 409)
                await self.repository.set_user_active(user_id, False)
                await self.repository.revoke_sessions_for_user(user_id, datetime.now(timezone.utc))
            else: await self.repository.set_user_active(user_id, True)
        return await self.get(user_id)

    async def set_roles(self, user_id: UUID, roles: set[str]) -> AdminUserView:
        row = await self.repository.get_user(user_id)
        if row is None: raise AdministrationError("user_not_found", 404)
        value, current = self._roles(roles), await self.repository.roles_for_user(user_id)
        if await self.repository.link_for_user(user_id) and "student" not in value:
            raise AdministrationError("student_link_requires_student_role", 409)
        if row.is_active and "admin" in current and "admin" not in value:
            await self.repository.lock_admin_invariant()
            if await self.repository.count_active_admins() <= 1: raise AdministrationError("last_active_admin", 409)
        await self.repository.replace_roles(user_id, value)
        return await self.get(user_id)

    async def link_student(self, user_id: UUID, student_id: UUID) -> AdminUserView:
        if await self.repository.get_user(user_id) is None: raise AdministrationError("user_not_found", 404)
        if not await self.repository.student_exists(student_id): raise AdministrationError("student_not_found", 404)
        if "student" not in await self.repository.roles_for_user(user_id): raise AdministrationError("student_link_requires_student_role", 409)
        if await self.repository.link_for_user(user_id) or await self.repository.link_for_student(student_id): raise AdministrationError("student_link_conflict", 409)
        try: await self.repository.create_student_link(user_id, student_id)
        except StudentLinkConflict as exc: raise AdministrationError("student_link_conflict", 409) from exc
        return await self.get(user_id)

    async def unlink_student(self, user_id: UUID) -> AdminUserView:
        if await self.repository.get_user(user_id) is None: raise AdministrationError("user_not_found", 404)
        await self.repository.remove_student_link(user_id)
        return await self.get(user_id)

    async def reset_password(self, user_id: UUID, new_password: str) -> None:
        if await self.repository.get_user(user_id) is None: raise AdministrationError("user_not_found", 404)
        try: encoded = self.password_hasher.hash_password(new_password)
        except InvalidPassword as exc: raise AdministrationError("invalid_account_input", 422) from exc
        await self.repository.update_password_hash(user_id, encoded)
        await self.repository.revoke_sessions_for_user(user_id, datetime.now(timezone.utc))

    async def bootstrap(self, *, login: str, display_name: str, password: str) -> AdminUserView:
        await self.repository.lock_admin_invariant()
        if await self.repository.count_active_admins(): raise AdministrationError("bootstrap_not_required", 409)
        return await self.create(login=login, display_name=display_name, password=password, roles={"admin"}, student_id=None)
