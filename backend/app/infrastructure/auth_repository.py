"""Narrow SQLAlchemy persistence API for later authentication phases."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.auth_models import AuthSession, StudentUserLink, User, UserRole


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
        self.session.add(row)
        await self.session.flush()
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
        self.session.add(row)
        await self.session.flush()
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
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def remove_student_link(self, user_id: UUID) -> bool:
        result = await self.session.execute(
            delete(StudentUserLink).where(StudentUserLink.user_id == user_id)
        )
        return bool(result.rowcount)
