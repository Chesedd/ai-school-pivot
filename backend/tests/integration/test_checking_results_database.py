import os
import pytest

pytestmark=pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),reason="TEST_DATABASE_URL is required for real PostgreSQL acceptance")

def test_phase_49_database_environment_is_postgresql():
    assert os.environ["TEST_DATABASE_URL"].startswith(("postgresql://","postgresql+asyncpg://"))
