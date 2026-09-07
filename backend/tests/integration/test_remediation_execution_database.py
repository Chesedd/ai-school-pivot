"""Real-PostgreSQL execution gate for C9B.

The full persistence scenario is intentionally marked at the execution boundary
when the dedicated database is not configured, matching the repository's PG gates.
"""
import os

import pytest


pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL unavailable")
def test_remediation_execution_database_boundary_is_configured():
    """The database-backed lifecycle suite may execute only against dedicated PG."""
    assert os.environ["TEST_DATABASE_URL"].startswith(("postgresql://", "postgresql+asyncpg://"))
