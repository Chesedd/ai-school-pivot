"""Dedicated PR12 PostgreSQL migration acceptance and lossless downgrade guard."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.integration.c10a_postgres import require_disposable_postgres
from tests.integration.test_manual_paper_review_database import manual, policies
from tests.integration.test_migrations import repository_head
from tests.integration.test_paper_checking_execution_database import execution_database
from tests.integration.test_paper_checking_review_database import completed
from tests.integration.test_paper_result_publication_migration import migrate

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("PR12_TEST_DB") != "manual_paper_review_pr12_migration_test",
        reason="dedicated PR12 migration gate required",
    ),
]


async def test_roundtrip_backfills_ai_and_preserves_history(tmp_path):
    url = require_disposable_postgres()
    engine = create_async_engine(url)
    seed = None
    try:
        async with engine.begin() as db:
            await db.execute(text("DROP SCHEMA public CASCADE"))
            await db.execute(text("CREATE SCHEMA public"))
        migrate(url, "upgrade", "head")
        seed = execution_database.__wrapped__(tmp_path)
        c = await anext(seed)
        run = await completed(c)
        from tests.integration.test_paper_checking_review_database import call

        await call(c, run, "start")
        await c[0].commit()
        await seed.aclose()
        seed = None
        migrate(url, "downgrade", "20261011_02")
        for direction, target in (
            ("upgrade", "20261011_03"),
            ("downgrade", "20261011_02"),
            ("upgrade", "20261011_03"),
        ):
            migrate(url, direction, target)
            async with engine.connect() as db:
                assert (
                    await db.scalar(text("SELECT version_num FROM alembic_version"))
                    == target
                )
                assert (
                    await db.scalar(text("SELECT count(*) FROM paper_check_reviews"))
                    == 1
                )
                assert (
                    await db.scalar(
                        text("SELECT count(*) FROM paper_check_review_revisions")
                    )
                    == 1
                )
                if target == "20261011_03":
                    assert (
                        await db.scalar(
                            text("SELECT source_kind FROM paper_check_reviews")
                        )
                        == "ai"
                    )
                    assert (
                        await db.scalar(
                            text("SELECT source_kind FROM paper_check_review_revisions")
                        )
                        == "ai"
                    )
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
        if seed:
            await seed.aclose()
        await engine.dispose()


async def test_downgrade_refuses_manual_data_without_changes(tmp_path):
    url = require_disposable_postgres()
    seed = execution_database.__wrapped__(tmp_path)
    try:
        c = await anext(seed)
        await policies(c)
        review = await manual(c)
        await c[0].commit()
        with pytest.raises(
            AssertionError, match="Cannot downgrade while manual paper reviews"
        ):
            migrate(url, "downgrade", "20261011_02")
        assert (
            await c[0].scalar(text("SELECT version_num FROM alembic_version"))
            == repository_head()
        )
        assert (
            await c[0].scalar(
                text("SELECT source_kind FROM paper_check_reviews WHERE id=:id"),
                {"id": review.review_id},
            )
            == "manual"
        )
    finally:
        await seed.aclose()
