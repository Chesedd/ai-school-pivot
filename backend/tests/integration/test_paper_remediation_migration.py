"""Dedicated PR11 migration database gate (never runs against suite fixtures)."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.c9ab_fixtures import seed_execution_world
from tests.integration.c10a_postgres import require_disposable_postgres
from tests.integration.test_migrations import repository_head
from tests.integration.test_paper_result_publication_migration import migrate

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("PR11_TEST_DB") != "paper_remediation_pr11_migration_test",
        reason="dedicated PR11 migration gate required",
    ),
]


async def test_paper_remediation_roundtrip_digital_rows_and_fresh_head():
    url = require_disposable_postgres()
    engine = create_async_engine(url)
    try:
        async with engine.begin() as db:
            await db.execute(text("DROP SCHEMA public CASCADE"))
            await db.execute(text("CREATE SCHEMA public"))
        migrate(url, "upgrade", "20261011_01")
        async with engine.begin() as db:
            ids = await seed_execution_world(db)
            count = await db.scalar(text("SELECT count(*) FROM remediation_plans"))
        for direction, target in [
            ("upgrade", "20261011_02"),
            ("downgrade", "20261011_01"),
            ("upgrade", "20261011_02"),
        ]:
            migrate(url, direction, target)
            async with engine.connect() as db:
                assert (
                    await db.scalar(text("SELECT version_num FROM alembic_version"))
                    == target
                )
                assert (
                    await db.scalar(text("SELECT count(*) FROM remediation_plans"))
                    == count
                )
                assert (
                    await db.scalar(
                        text(
                            "SELECT source_submission_id FROM remediation_plans WHERE id=:plan_0"
                        ),
                        ids,
                    )
                    == ids["source_submission_0"]
                )
        # This database is expressly dedicated to destructive migration acceptance.
        async with engine.begin() as db:
            await db.execute(text("DROP SCHEMA public CASCADE"))
            await db.execute(text("CREATE SCHEMA public"))
        migrate(url, "upgrade", "head")
        async with engine.connect() as db:
            assert (
                await db.scalar(text("SELECT version_num FROM alembic_version"))
                == repository_head()
            )
    finally:
        await engine.dispose()


async def test_downgrade_refuses_paper_provenance(tmp_path):
    from tests.integration.test_paper_checking_execution_database import (
        execution_database,
    )
    from tests.integration.test_paper_remediation_database import create, published

    url = require_disposable_postgres()
    seed = execution_database.__wrapped__(tmp_path)
    try:
        context = await anext(seed)
        _, _, pub, _ = await published(context)
        plan = await create(context, pub)
        await context[0].commit()
        with pytest.raises(
            AssertionError, match="Paper downgrade blocked"
        ):
            migrate(url, "downgrade", "20261011_01")
        assert (
            await context[0].scalar(text("SELECT version_num FROM alembic_version"))
            == repository_head()
        )
        assert (
            await context[0].scalar(
                text(
                    "SELECT source_paper_publication_id FROM remediation_plans WHERE id=:id"
                ),
                {"id": plan["id"]},
            )
            == pub["id"]
        )
    finally:
        await seed.aclose()
