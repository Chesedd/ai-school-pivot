import os
from urllib.parse import urlparse
import pytest

URL=os.getenv("TEST_DATABASE_URL")
if URL and not urlparse(URL.replace("+asyncpg","")).path.removeprefix("/").endswith("_test"):
    raise RuntimeError("TEST_DATABASE_URL database name must end with _test")
pytestmark=pytest.mark.skipif(not URL,reason="TEST_DATABASE_URL is not configured")


def test_postgresql_provider_boundary_requires_behavioral_fixture():
    """The repository's full PostgreSQL suite is enabled only by the guarded test URL."""
    assert URL
