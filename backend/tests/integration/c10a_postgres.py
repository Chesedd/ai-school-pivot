"""Small, deliberately boring helpers for the C10A PostgreSQL acceptance tests."""

import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


def require_disposable_postgres() -> str:
    """Fail closed before any acceptance test is allowed to mutate a database."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required")
    url = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlsplit(url)
    database = parsed.path.removeprefix("/")
    if parsed.scheme != "postgresql" or not database.endswith("_test"):
        raise RuntimeError(
            "C10A requires a PostgreSQL TEST_DATABASE_URL whose database ends in _test"
        )
    return TEST_DATABASE_URL if "+asyncpg" in TEST_DATABASE_URL else TEST_DATABASE_URL.replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


@asynccontextmanager
async def rolled_back_connection():
    """Give a test an isolated transaction and always roll it back."""
    engine = create_async_engine(require_disposable_postgres())
    connection = await engine.connect()
    transaction = await connection.begin()
    try:
        yield connection
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def assert_tables(connection, *names: str) -> None:
    found = set((await connection.execute(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name = ANY(:names)"
    ), {"names": list(names)})).scalars())
    assert found == set(names)


async def assert_constraints(connection, *names: str) -> None:
    found = set((await connection.execute(text(
        "SELECT conname FROM pg_constraint WHERE conname = ANY(:names)"
    ), {"names": list(names)})).scalars())
    assert found == set(names)
