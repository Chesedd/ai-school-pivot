"""Process-level PR9 migration gate; requires a disposable PostgreSQL database."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.c10a_postgres import require_disposable_postgres

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]


def migrate(url, *arguments):
    environment = {**os.environ, "DATABASE_URL": url}
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


async def test_paper_result_publication_migration_roundtrip_and_fresh_head():
    url = require_disposable_postgres()
    engine = create_async_engine(url)

    async def at(revision):
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == revision
            )

    try:
        migrate(url, "downgrade", "20261010_01")
        await at("20261010_01")
        migrate(url, "upgrade", "20261010_02")
        await at("20261010_02")
        migrate(url, "downgrade", "20261010_01")
        await at("20261010_01")
        migrate(url, "upgrade", "20261010_02")
        await at("20261010_02")
        # This process-level gate runs separately from rollback-isolated fixtures.
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
        migrate(url, "upgrade", "head")
        await at("20261010_02")
    finally:
        migrate(url, "upgrade", "head")
        await engine.dispose()
