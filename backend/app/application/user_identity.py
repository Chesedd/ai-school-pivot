"""Canonical handling for optional structured human names."""

from __future__ import annotations

MAX_PERSON_NAME_LENGTH = 100
MAX_DISPLAY_NAME_LENGTH = 200


class InvalidPersonName(ValueError):
    """A structured name cannot be represented by the account schema."""


def normalize_person_name(value: str | None) -> str | None:
    """Trim one nullable name component and enforce its persistence bound."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidPersonName
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_PERSON_NAME_LENGTH:
        raise InvalidPersonName
    return normalized


def compose_display_name(first_name: str | None, last_name: str | None) -> str:
    """Compose the sole canonical display form for structured user names."""
    first = normalize_person_name(first_name)
    last = normalize_person_name(last_name)
    display_name = " ".join(part for part in (first, last) if part is not None)
    if not display_name or len(display_name) > MAX_DISPLAY_NAME_LENGTH:
        raise InvalidPersonName
    return display_name
