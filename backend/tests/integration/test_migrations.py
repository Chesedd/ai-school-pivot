"""Focused PostgreSQL migration regression coverage."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("migration tests require a database ending in _test")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required"),
]

BACKEND = Path(__file__).parents[2]


def upgrade(revision: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = URL
    subprocess.run(
        ["alembic", "upgrade", revision],
        cwd=BACKEND,
        env=environment,
        check=True,
    )


async def test_upgrade_from_authoring_baseline_backfills_review_history():
    engine = create_async_engine(URL)
    owner_id = uuid4()
    session_id = uuid4()
    review_id = uuid4()
    try:
        # This test owns a disposable *_test database and deliberately verifies the
        # supported baseline rather than attempting to repair older migrations.
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))

        upgrade("20260823_02")
        upgrade("20260823_03")
        upgrade("20260823_04")

        async with engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO authoring_sessions
                    (id, owner_id, schema_version, policy_version, frozen_request,
                     request_fingerprint, frozen_allowlist)
                    VALUES (:id, :owner, 'v1', 'v1', '{}'::jsonb, :fingerprint,
                            '[]'::jsonb)"""),
                {"id": session_id, "owner": owner_id, "fingerprint": "a" * 64},
            )
            await connection.execute(
                text("""INSERT INTO authoring_reviews
                    (id, session_id, owner_id, draft, version)
                    VALUES (:id, :session, :owner, '{"prompt":"legacy"}'::jsonb, 3)"""),
                {"id": review_id, "session": session_id, "owner": owner_id},
            )

        for revision in ("20260823_05", "20260824_01", "20260824_02", "head"):
            upgrade(revision)

        async with engine.connect() as connection:
            result = (await connection.execute(
                text("""SELECT change_summary
                    FROM authoring_review_revisions
                    WHERE review_id = :review"""),
                {"review": review_id},
            )).scalar_one()

        assert result == {
            "source": "legacy_backfill",
            "history_available": False,
        }
    finally:
        await engine.dispose()
