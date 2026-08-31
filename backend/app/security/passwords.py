"""Argon2id password hashing behind a deliberately narrow interface."""

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MAX_PASSWORD_BYTES = 1024


class InvalidPassword(ValueError):
    """The password does not satisfy the bounded input policy."""


def validate_password(password: str) -> None:
    """Reject empty or oversized passwords without altering their contents."""
    if not isinstance(password, str) or not password:
        raise InvalidPassword("password must not be empty")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise InvalidPassword("password exceeds the maximum encoded length")


class PasswordHasher:
    """Create and verify self-describing Argon2id encoded hashes."""

    def __init__(self, implementation: Argon2PasswordHasher | None = None) -> None:
        self._implementation = implementation or Argon2PasswordHasher()

    def hash_password(self, password: str) -> str:
        validate_password(password)
        return self._implementation.hash(password)

    def verify_password(self, password: str, encoded_hash: str) -> bool:
        try:
            validate_password(password)
            return self._implementation.verify(encoded_hash, password)
        except (InvalidPassword, InvalidHashError, VerificationError, TypeError):
            return False

    def needs_rehash(self, encoded_hash: str) -> bool:
        try:
            return self._implementation.check_needs_rehash(encoded_hash)
        except (InvalidHashError, TypeError):
            return False
