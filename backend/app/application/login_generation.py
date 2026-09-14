"""Canonical policy for allocating logins from structured human names."""

from __future__ import annotations

import unicodedata

from app.application.auth_errors import InvalidAccountInputError
from app.application.authentication import MAX_LOGIN_LENGTH, normalize_login

MAX_GENERATED_LOGIN_ATTEMPTS = 100


def _login_component(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidAccountInputError()
    value = unicodedata.normalize("NFKC", value.strip()).casefold()
    result: list[str] = []
    separator = False
    for character in value:
        category = unicodedata.category(character)
        if character.isspace() or character == "-":
            separator = bool(result)
        elif category[0] in {"L", "N"}:
            if separator:
                result.append("-")
            result.append(character)
            separator = False
        # Other punctuation and symbols are deliberately discarded.
    return "".join(result).strip("-")


def generated_login_base(first_name: str, last_name: str) -> str:
    """Build the unsuffixed, normalized login from exactly two name parts."""
    first, last = _login_component(first_name), _login_component(last_name)
    if not first or not last:
        raise InvalidAccountInputError()
    base = f"{first}.{last}"
    # NFKC may expand input, so enforce the login column's bound here as well.
    base = base[:MAX_LOGIN_LENGTH].rstrip("-.")
    if "." not in base or base.endswith("."):
        raise InvalidAccountInputError()
    return normalize_login(base)[0]


def generated_login_candidate(base: str, attempt: int) -> str:
    """Return base, then base-2, base-3, while preserving the length bound."""
    if attempt < 1:
        raise ValueError("attempt must be positive")
    suffix = "" if attempt == 1 else f"-{attempt}"
    candidate = base[: MAX_LOGIN_LENGTH - len(suffix)].rstrip("-.") + suffix
    return normalize_login(candidate)[0]
