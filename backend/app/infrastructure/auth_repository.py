"""Narrow SQLAlchemy persistence API for later authentication phases."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.auth_models import AuthSession, StudentUserLink, User, UserRole


class DuplicateNormalizedLogin(Exception):
    """Canonical login already exists (without leaking database details)."""


class SessionTokenCollision(Exception):
    """A generated session digest collided with an existing digest."""


class StudentLinkConflict(Exception):
    """A one-to-one student link conflicted with an existing link."""


def _constraint_name(exc: IntegrityError) -> str | None:
    """Read asyncpg's constraint metadata through SQLAlchemy's wrapper."""
    original = getattr(exc, "orig", None)
    return getattr(original, "constraint_name", None) or getattr(
        getattr(original, "__cause__", None), "constraint_name", None
    )


class SQLAlchemyAuthRepository:
    """Persist account facts without authentication or authorization policy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_user(
        self,
        *,
        login: str,
        normalized_login: str,
        display_name: str,
        password_hash: str,
    ) -> User:
        row = User(
            login=login,
            normalized_login=normalized_login,
            display_name=display_name,
            password_hash=password_hash,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == "uq_users_normalized_login":
                raise DuplicateNormalizedLogin from exc
            raise
        await self.session.refresh(row)
        return row

    async def get_user(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def find_user_by_normalized_login(self, normalized_login: str) -> User | None:
        return await self.session.scalar(
            select(User).where(User.normalized_login == normalized_login)
        )

    async def roles_for_user(self, user_id: UUID) -> frozenset[str]:
        rows = await self.session.scalars(
            select(UserRole.role).where(UserRole.user_id == user_id)
        )
        return frozenset(rows)

    async def add_role(self, user_id: UUID, role: str) -> None:
        self.session.add(UserRole(user_id=user_id, role=role))
        await self.session.flush()

    async def remove_role(self, user_id: UUID, role: str) -> bool:
        result = await self.session.execute(
            delete(UserRole).where(UserRole.user_id == user_id, UserRole.role == role)
        )
        return bool(result.rowcount)

    async def create_session(
        self, *, user_id: UUID, token_hash: bytes, expires_at: datetime
    ) -> AuthSession:
        row = AuthSession(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == "uq_auth_sessions_token_hash":
                raise SessionTokenCollision from exc
            raise
        await self.session.refresh(row)
        return row

    async def find_session_by_token_hash(self, token_hash: bytes) -> AuthSession | None:
        return await self.session.scalar(
            select(AuthSession).where(AuthSession.token_hash == token_hash)
        )

    async def revoke_session(self, session_id: UUID, revoked_at: datetime) -> bool:
        result = await self.session.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        return bool(result.rowcount)

    async def delete_expired_sessions(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(AuthSession).where(AuthSession.expires_at <= cutoff)
        )
        return int(result.rowcount or 0)

    async def link_for_user(self, user_id: UUID) -> StudentUserLink | None:
        return await self.session.get(StudentUserLink, user_id)

    async def link_for_student(self, student_id: UUID) -> StudentUserLink | None:
        return await self.session.scalar(
            select(StudentUserLink).where(StudentUserLink.student_id == student_id)
        )

    async def create_student_link(
        self, user_id: UUID, student_id: UUID
    ) -> StudentUserLink:
        row = StudentUserLink(user_id=user_id, student_id=student_id)
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError as exc:
            raise StudentLinkConflict from exc
        await self.session.refresh(row)
        return row

    async def remove_student_link(self, user_id: UUID) -> bool:
        result = await self.session.execute(
            delete(StudentUserLink).where(StudentUserLink.user_id == user_id)
        )
        return bool(result.rowcount)

    async def list_users(self, *, offset: int, limit: int) -> tuple[list[User], int]:
        total = await self.session.scalar(select(func.count()).select_from(User))
        rows = await self.session.scalars(
            select(User).order_by(User.created_at, User.id).offset(offset).limit(limit)
        )
        return list(rows), int(total or 0)

    async def update_user_identity(
        self, user_id: UUID, *, login: str, normalized_login: str, display_name: str
    ) -> User:
        row = await self.get_user(user_id)
        assert row is not None
        row.login, row.normalized_login, row.display_name = login, normalized_login, display_name
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == "uq_users_normalized_login":
                raise DuplicateNormalizedLogin from exc
            raise
        await self.session.refresh(row)
        return row

    async def set_user_active(self, user_id: UUID, active: bool) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(is_active=active, updated_at=func.clock_timestamp())
        )

    async def replace_roles(self, user_id: UUID, roles: frozenset[str]) -> None:
        await self.session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        self.session.add_all(UserRole(user_id=user_id, role=role) for role in sorted(roles))
        await self.session.flush()

    async def revoke_sessions_for_user(self, user_id: UUID, revoked_at: datetime) -> int:
        result = await self.session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        return int(result.rowcount or 0)

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(password_hash=password_hash, updated_at=func.clock_timestamp())
        )

    async def student_exists(self, student_id: UUID) -> bool:
        from app.infrastructure.assessment_models import Student
        return await self.session.get(Student, student_id) is not None

    async def lock_admin_invariant(self) -> None:
        # A transaction-scoped PostgreSQL advisory lock serializes every operation
        # that can reduce the active-admin set (and bootstrap). It works across
        # application processes and is released automatically on commit/rollback.
        await self.session.execute(text("SELECT pg_advisory_xact_lock(71431001)"))

    async def count_active_admins(self) -> int:
        value = await self.session.scalar(
            select(func.count()).select_from(UserRole).join(User, User.id == UserRole.user_id)
            .where(UserRole.role == "admin", User.is_active.is_(True))
        )
        return int(value or 0)
