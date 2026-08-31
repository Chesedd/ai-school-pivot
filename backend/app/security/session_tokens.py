"""Opaque session secret generation and its single at-rest representation."""

import hashlib
import secrets

SESSION_RANDOM_BYTES = 32
MAX_SESSION_SECRET_BYTES = 256


class InvalidSessionSecret(ValueError):
    """A presented session secret is absent or outside its input bound."""


def generate_session_secret() -> str:
    """Return a URL-safe opaque value backed by 256 bits of secure randomness."""
    return secrets.token_urlsafe(SESSION_RANDOM_BYTES)


def hash_session_secret(secret: str) -> bytes:
    """Return the canonical raw 32-byte SHA-256 digest for a valid secret."""
    if not isinstance(secret, str) or not secret:
        raise InvalidSessionSecret("invalid session secret")
    try:
        encoded = secret.encode("ascii")
    except UnicodeEncodeError as exc:
        raise InvalidSessionSecret("invalid session secret") from exc
    if len(encoded) > MAX_SESSION_SECRET_BYTES:
        raise InvalidSessionSecret("invalid session secret")
    return hashlib.sha256(encoded).digest()
