from uuid import uuid4

import pytest

from app.domain.catalog import CatalogLifecycle, CatalogLifecycleState
from app.infrastructure.models import normalize_catalog_name


def test_provisional_lifecycle_requires_authenticated_proposer():
    with pytest.raises(ValueError, match="require a proposer"):
        CatalogLifecycleState(CatalogLifecycle.PROVISIONAL)
    actor = uuid4()
    assert (
        CatalogLifecycleState(CatalogLifecycle.PROVISIONAL, actor).proposed_by == actor
    )


def test_lifecycle_rejects_untyped_arbitrary_strings():
    with pytest.raises(TypeError, match="CatalogLifecycle"):
        CatalogLifecycleState("active")  # type: ignore[arg-type]


def test_catalog_normalization_matches_established_semantics():
    assert normalize_catalog_name("  ТЕОРЕМА—ВИЁТА! ") == "теорема виета"
