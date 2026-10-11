"""Process-level PR10 asyncpg downgrade/upgrade and fresh-schema gate."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.integration.c10a_postgres import require_disposable_postgres
from tests.integration.test_paper_checking_execution_database import (
    execution_database,
    start,
)
from tests.integration.test_paper_result_publication_migration import migrate

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required")]


async def test_paper_batch_migration_roundtrip_and_fresh_head(tmp_path):
    url = require_disposable_postgres()
    engine = create_async_engine(url)

    async def at(revision):
        async with engine.connect() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version")) == revision
            definition = await db.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_scan_checking_events_type'"))
            assert ("batch.checking_started" in definition) == (revision in {"20261011_01", "20261011_02"})

    try:
        # Prove rollback does not require deleting new audit history on a
        # populated disposable database. Ordinary acceptance fixtures still roll back.
        migrate(url, "upgrade", "head")
        seed = execution_database.__wrapped__(tmp_path)
        try:
            context = await anext(seed)
            await start(context)
            await context[0].commit()
            batch_id = context[2]["batch"]
        finally:
            await seed.aclose()
        migrate(url, "downgrade", "20261010_02")
        await at("20261010_02")
        async with engine.connect() as db:
            assert await db.scalar(text("SELECT count(*) FROM scan_checking_events WHERE batch_id=:id AND event_type='batch.checking_started'"), {"id": batch_id}) == 1
            assert await db.scalar(text("SELECT convalidated FROM pg_constraint WHERE conname='ck_scan_checking_events_type'")) is False
        migrate(url, "upgrade", "20261011_01")
        await at("20261011_01")
        migrate(url, "downgrade", "20261010_02")
        await at("20261010_02")
        migrate(url, "upgrade", "20261011_01")
        await at("20261011_01")
        async with engine.begin() as db:
            await db.execute(text("DROP SCHEMA public CASCADE"))
            await db.execute(text("CREATE SCHEMA public"))
        migrate(url, "upgrade", "head")
        from tests.integration.test_migrations import repository_head
        await at(repository_head())
    finally:
        migrate(url, "upgrade", "head")
        await engine.dispose()
