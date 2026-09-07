"""PostgreSQL acceptance entry point for test_classroom_results_database.

Scenarios use the shared migration/database harness and are intentionally collected
without requiring a database connection at import time.
"""
import os
import pytest

@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="real PostgreSQL TEST_DATABASE_URL required")
def test_real_postgresql_acceptance_environment_is_configured():
    assert os.environ["TEST_DATABASE_URL"].startswith(("postgresql://", "postgresql+asyncpg://"))
