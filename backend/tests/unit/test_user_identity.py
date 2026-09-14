import pytest

from app.application.user_identity import (
    InvalidPersonName,
    compose_display_name,
    normalize_person_name,
)


def test_display_name_composition_is_centralized_and_ordered():
    assert compose_display_name("  Ada ", " Lovelace ") == "Ada Lovelace"
    assert compose_display_name("Prince", None) == "Prince"
    assert compose_display_name(None, "Madonna") == "Madonna"


def test_name_components_enforce_trimmed_hundred_character_bound():
    assert normalize_person_name(" x ") == "x"
    assert normalize_person_name("x" * 100) == "x" * 100
    for invalid in ("", "   ", "x" * 101):
        with pytest.raises(InvalidPersonName):
            normalize_person_name(invalid)
    with pytest.raises(InvalidPersonName):
        compose_display_name("x" * 100, "y" * 100)
