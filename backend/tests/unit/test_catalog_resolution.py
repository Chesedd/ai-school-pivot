from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.catalog_resolution import CatalogResolutionService


@pytest.mark.parametrize(
    ("results", "expected", "query_count"),
    [
        ([None, None, None, None], False, 4),
        ([None, object()], True, 2),
    ],
)
async def test_in_use_checks_references_sequentially(results, expected, query_count):
    session = SimpleNamespace(scalar=AsyncMock(side_effect=results))
    service = CatalogResolutionService(SimpleNamespace(session=session))

    assert await service._in_use("subject", uuid4()) is expected
    assert session.scalar.await_count == query_count
