"""HTTP-independent password and opaque-session authentication services.

Logins use ``NFKC(trimmed_login).casefold()``.  This compatibility-normalizes
Unicode without transliteration or punctuation removal, then enforces the C1
254-character persistence bound.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from app.application.auth_errors import (
    AccountAlreadyExistsError,
    ExpiredSessionError,
    InactiveAccountError,
    InvalidAccountInputError,
    InvalidCredentialsError,
    InvalidSessionError,
    SessionCreationError,
)
from app.infrastructure.auth_repository import (
    DuplicateNormalizedLogin,
    SessionTokenCollision,
)
from app.security.passwords import InvalidPassword, PasswordHasher
from app.security.session_tokens import (
    InvalidSessionSecret,
    generate_session_secret,
    hash_session_secret,
)

MAX_LOGIN_LENGTH = 254
MAX_DISPLAY_NAME_LENGTH = 200
MAX_SESSION_CREATION_ATTEMPTS = 3


class UserRecord(Protocol):
    id: UUID
    login: str
    display_name: str
    password_hash: str
    is_active: bool


class SessionRecord(Protocol):
    id: UUID
    user_id: UUID
    expires_at: datetime
    revoked_at: datetime | None


class AuthRepository(Protocol):
    async def create_user(
        self,
        *,
        login: str,
        normalized_login: str,
        display_name: str,
        password_hash: str,
    ) -> UserRecord: ...
    async def get_user(self, user_id: UUID) -> UserRecord | None: ...
    async def find_user_by_normalized_login(
        self, normalized_login: str
    ) -> UserRecord | None: ...
    async def create_session(
        self, *, user_id: UUID, token_hash: bytes, expires_at: datetime
    ) -> SessionRecord: ...
    async def find_session_by_token_hash(
        self, token_hash: bytes
    ) -> SessionRecord | None: ...
    async def revoke_session(self, session_id: UUID, revoked_at: datetime) -> bool: ...


def normalize_login(login: str) -> tuple[str, str]:
    """Return the trimmed display login and its NFKC/casefold identity."""
    if not isinstance(login, str):
        raise InvalidAccountInputError()
    visible = login.strip()
    canonical = unicodedata.normalize("NFKC", visible).casefold()
    if (
        not visible
        or len(visible) > MAX_LOGIN_LENGTH
        or not canonical
        or len(canonical) > MAX_LOGIN_LENGTH
        or canonical != canonical.strip()
    ):
        raise InvalidAccountInputError()
    return visible, canonical


@dataclass(frozen=True)
class AuthenticatedAccount:
    user_id: UUID
    login: str
    display_name: str
    is_active: bool


@dataclass(frozen=True)
class SessionIssuance:
    session_secret: str = field(repr=False)
    expires_at: datetime
    user: AuthenticatedAccount


def _safe_user(user: UserRecord) -> AuthenticatedAccount:
    return AuthenticatedAccount(user.id, user.login, user.display_name, user.is_active)


class AuthenticationService:
    def __init__(
        self,
        repository: AuthRepository,
        *,
        password_hasher: PasswordHasher | None = None,
        session_ttl: timedelta = timedelta(hours=12),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        secret_generator: Callable[[], str] = generate_session_secret,
    ) -> None:
        if session_ttl <= timedelta(0) or session_ttl > timedelta(days=30):
            raise ValueError("session TTL must be between zero and 30 days")
        self.repository = repository
        self.password_hasher = password_hasher or PasswordHasher()
        # Unknown accounts still exercise the verifier, reducing lookup timing as
        # an account-enumeration signal without exposing a reusable credential.
        self._dummy_password_hash = self.password_hasher.hash_password(
            "authentication-timing-placeholder"
        )
        self.session_ttl = session_ttl
        self.clock = clock
        self.secret_generator = secret_generator

    async def create_account(
        self, *, login: str, display_name: str, password: str
    ) -> AuthenticatedAccount:
        visible_login, normalized_login = normalize_login(login)
        if not isinstance(display_name, str):
            raise InvalidAccountInputError()
        display_name = display_name.strip()
        if not display_name or len(display_name) > MAX_DISPLAY_NAME_LENGTH:
            raise InvalidAccountInputError()
        try:
            password_hash = self.password_hasher.hash_password(password)
            user = await self.repository.create_user(
                login=visible_login,
                normalized_login=normalized_login,
                display_name=display_name,
                password_hash=password_hash,
            )
        except InvalidPassword as exc:
            raise InvalidAccountInputError() from exc
        except DuplicateNormalizedLogin as exc:
            raise AccountAlreadyExistsError() from exc
        return _safe_user(user)

    async def authenticate(self, login: str, password: str) -> AuthenticatedAccount:
        try:
            _, normalized_login = normalize_login(login)
        except InvalidAccountInputError as exc:
            raise InvalidCredentialsError() from exc
        user = await self.repository.find_user_by_normalized_login(normalized_login)
        if user is None:
            self.password_hasher.verify_password(password, self._dummy_password_hash)
            raise InvalidCredentialsError()
        if not user.is_active:
            raise InactiveAccountError()
        if not self.password_hasher.verify_password(password, user.password_hash):
            raise InvalidCredentialsError()
        return _safe_user(user)

    async def login(self, login: str, password: str) -> SessionIssuance:
        user = await self.authenticate(login, password)
        return await self.create_session(user.user_id, user=user)

    async def create_session(
        self, user_id: UUID, *, user: AuthenticatedAccount | None = None
    ) -> SessionIssuance:
        if user is None:
            row = await self.repository.get_user(user_id)
            if row is None:
                raise InvalidCredentialsError()
            if not row.is_active:
                raise InactiveAccountError()
            user = _safe_user(row)
        now = self._now()
        expires_at = now + self.session_ttl
        for _ in range(MAX_SESSION_CREATION_ATTEMPTS):
            secret = self.secret_generator()
            try:
                digest = hash_session_secret(secret)
                await self.repository.create_session(
                    user_id=user_id, token_hash=digest, expires_at=expires_at
                )
                return SessionIssuance(secret, expires_at, user)
            except (SessionTokenCollision, InvalidSessionSecret):
                continue
        raise SessionCreationError()

    async def resolve_session(self, session_secret: str) -> AuthenticatedAccount:
        try:
            digest = hash_session_secret(session_secret)
        except InvalidSessionSecret as exc:
            raise InvalidSessionError() from exc
        session = await self.repository.find_session_by_token_hash(digest)
        if session is None or session.revoked_at is not None:
            raise InvalidSessionError()
        if session.expires_at <= self._now():
            raise ExpiredSessionError()
        user = await self.repository.get_user(session.user_id)
        if user is None:
            raise InvalidSessionError()
        if not user.is_active:
            raise InactiveAccountError()
        return _safe_user(user)

    async def logout(self, session_secret: str) -> None:
        """Revoke if present; invalid, expired, and repeated logout all succeed."""
        try:
            digest = hash_session_secret(session_secret)
        except InvalidSessionSecret:
            return
        session = await self.repository.find_session_by_token_hash(digest)
        if session is not None and session.revoked_at is None:
            await self.repository.revoke_session(session.id, self._now())

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("authentication clock must return timezone-aware time")
        return value.astimezone(timezone.utc)
