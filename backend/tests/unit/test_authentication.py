from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.application.auth_errors import (
    ExpiredSessionError,
    InactiveAccountError,
    InvalidAccountInputError,
    InvalidCredentialsError,
    InvalidSessionError,
    SessionCreationError,
)
from app.application.authentication import AuthenticationService, normalize_login
from app.infrastructure.auth_repository import SessionTokenCollision
from app.security.passwords import MAX_PASSWORD_BYTES, InvalidPassword, PasswordHasher
from app.security.session_tokens import generate_session_secret, hash_session_secret

NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)


@dataclass
class User:
    id: UUID
    login: str
    normalized_login: str
    display_name: str
    password_hash: str
    is_active: bool = True


@dataclass
class Session:
    id: UUID
    user_id: UUID
    token_hash: bytes
    expires_at: datetime
    revoked_at: datetime | None = None


class FakeRepository:
    def __init__(self):
        self.users = {}
        self.sessions = {}
        self.created_hashes = []
        self.collisions = 0

    async def create_user(self, **values):
        user = User(uuid4(), **values)
        self.users[user.normalized_login] = user
        return user

    async def get_user(self, user_id):
        return next((u for u in self.users.values() if u.id == user_id), None)

    async def find_user_by_normalized_login(self, normalized_login):
        return self.users.get(normalized_login)

    async def create_session(self, *, user_id, token_hash, expires_at):
        self.created_hashes.append(token_hash)
        if self.collisions:
            self.collisions -= 1
            raise SessionTokenCollision
        row = Session(uuid4(), user_id, token_hash, expires_at)
        self.sessions[token_hash] = row
        return row

    async def find_session_by_token_hash(self, token_hash):
        return self.sessions.get(token_hash)

    async def revoke_session(self, session_id, revoked_at):
        row = next(s for s in self.sessions.values() if s.id == session_id)
        if row.revoked_at is not None:
            return False
        row.revoked_at = revoked_at
        return True


def test_login_normalization_is_bounded_unicode_aware_and_deterministic():
    assert normalize_login(" Teacher ") == ("Teacher", "teacher")
    assert normalize_login("Teacher")[1] == normalize_login("teacher")[1]
    assert normalize_login("STRASSE")[1] == normalize_login("Straße")[1]
    assert normalize_login("Ｔｅａｃｈｅｒ")[1] == "teacher"
    assert normalize_login("École") == normalize_login("École")
    for invalid in ("", "  ", "x" * 255):
        with pytest.raises(InvalidAccountInputError):
            normalize_login(invalid)


def test_password_hashing_policy_salts_verifies_and_fails_safely():
    hasher = PasswordHasher()
    password = " secret password "
    first = hasher.hash_password(password)
    second = hasher.hash_password(password)
    assert first != password and second != password and first != second
    assert first.startswith("$argon2id$")
    assert hasher.verify_password(password, first)
    assert not hasher.verify_password("wrong", first)
    assert not hasher.verify_password(password, "malformed")
    assert not hasher.needs_rehash("malformed")
    with pytest.raises(InvalidPassword) as error:
        hasher.hash_password("")
    assert password not in str(error.value)
    with pytest.raises(InvalidPassword):
        hasher.hash_password("x" * (MAX_PASSWORD_BYTES + 1))


def test_session_secret_generation_and_digest_shape():
    secrets = {generate_session_secret() for _ in range(32)}
    assert len(secrets) == 32
    secret = next(iter(secrets))
    digest = hash_session_secret(secret)
    assert len(digest) == 32 and isinstance(digest, bytes)
    assert secret.encode() != digest
    assert digest == hash_session_secret(secret)
    assert digest != hash_session_secret(secret + "x")


async def make_service(active=True, secrets=None):
    repository = FakeRepository()
    values = iter(secrets or ["A" * 43])
    service = AuthenticationService(
        repository, clock=lambda: NOW, secret_generator=lambda: next(values)
    )
    user = await service.create_account(
        login=" Teacher ", display_name=" Teacher One ", password="correct"
    )
    repository.users["teacher"].is_active = active
    return service, repository, user


async def test_login_persists_only_digest_and_returns_safe_secret_once():
    service, repository, user = await make_service()
    issued = await service.login("TEACHER", "correct")
    assert issued.user == user and issued.session_secret == "A" * 43
    assert issued.expires_at == NOW + timedelta(hours=12)
    assert repository.created_hashes == [hash_session_secret(issued.session_secret)]
    assert issued.session_secret not in repr(issued)
    assert not hasattr(issued.user, "password_hash")


async def test_login_errors_are_bounded_and_inactive_is_rejected():
    service, _, _ = await make_service()
    for login, password in (("unknown", "correct"), ("teacher", "wrong")):
        with pytest.raises(InvalidCredentialsError) as error:
            await service.login(login, password)
        assert str(error.value) == "invalid_credentials"
        assert password not in str(error.value)
    inactive, _, _ = await make_service(active=False)
    with pytest.raises(InactiveAccountError):
        await inactive.login("teacher", "correct")


async def test_session_resolution_logout_expiry_revocation_and_disable():
    service, repository, user = await make_service()
    issued = await service.login("teacher", "correct")
    assert await service.resolve_session(issued.session_secret) == user
    with pytest.raises(InvalidSessionError):
        await service.resolve_session("unknown")
    row = repository.sessions[hash_session_secret(issued.session_secret)]
    row.expires_at = NOW
    with pytest.raises(ExpiredSessionError):
        await service.resolve_session(issued.session_secret)
    row.expires_at = NOW + timedelta(hours=1)
    repository.users["teacher"].is_active = False
    with pytest.raises(InactiveAccountError):
        await service.resolve_session(issued.session_secret)
    repository.users["teacher"].is_active = True
    await service.logout(issued.session_secret)
    await service.logout(issued.session_secret)
    await service.logout("not-present")
    with pytest.raises(InvalidSessionError):
        await service.resolve_session(issued.session_secret)


async def test_token_collision_retry_is_strictly_bounded():
    service, repository, _ = await make_service(secrets=["A" * 43, "B" * 43, "C" * 43])
    repository.collisions = 2
    issued = await service.login("teacher", "correct")
    assert issued.session_secret == "C" * 43 and len(repository.created_hashes) == 3

    failed, failed_repository, _ = await make_service(
        secrets=["A" * 43, "B" * 43, "C" * 43]
    )
    failed_repository.collisions = 3
    with pytest.raises(SessionCreationError):
        await failed.login("teacher", "correct")
    assert len(failed_repository.created_hashes) == 3
