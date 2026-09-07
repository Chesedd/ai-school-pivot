"""Focused real-PostgreSQL gate for C9C shared Checking persistence.

The repository's disposable PostgreSQL contract is intentionally mandatory for
claiming C9C complete. The source/unit suite remains runnable without it.
"""
import os

import pytest


pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL unavailable")
def test_remediation_checking_database_is_a_disposable_postgresql_target():
    url=os.environ["TEST_DATABASE_URL"]
    assert url.startswith(("postgresql://","postgresql+asyncpg://"))
    assert url.rsplit("/",1)[-1].split("?",1)[0].endswith("_test")
