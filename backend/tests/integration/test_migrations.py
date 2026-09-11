"""Focused PostgreSQL migration regression coverage."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine


URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("migration tests require a database ending in _test")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required"),
]

BACKEND = Path(__file__).parents[2]


def repository_head() -> str:
    """Resolve the sole head of the migration graph in this checkout."""
    config = Config(BACKEND / "alembic.ini")
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"expected a single Alembic head, found {heads}"
    return heads[0]


async def assert_database_at_repository_head(engine) -> None:
    """Verify the database revision, rather than Alembic's display text."""
    async with engine.connect() as connection:
        revisions = (await connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        )).scalars().all()
    assert revisions == [repository_head()]


def alembic(*arguments: str) -> str:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = URL
    command = ["alembic", *arguments]
    try:
        completed = subprocess.run(command, cwd=BACKEND, env=environment,
            check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            f"Alembic command failed: {' '.join(command)}\n"
            f"stdout:\n{exc.stdout}\n\nstderr:\n{exc.stderr}"
        ) from exc
    return completed.stdout


@pytest_asyncio.fixture(autouse=True)
async def restore_current_schema():
    """Leave the shared integration database complete, even after failures."""
    yield
    engine = create_async_engine(URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()
    alembic("upgrade", "head")


async def test_clean_database_upgrades_to_head_with_observability_columns():
    """Observability columns are introduced by their owning revision only."""
    engine = create_async_engine(URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))

        alembic("upgrade", "20260810_01")

        async with engine.connect() as connection:
            foundation_columns = (await connection.execute(sa.text("""
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'check_results'
                  AND column_name IN (
                      'reason_code', 'confidence_policy_version',
                      'confidence_details'
                  )
                ORDER BY column_name
            """))).all()

        assert foundation_columns == []

        alembic("upgrade", "20260819_01")
        async with engine.connect() as connection:
            observability_columns = (await connection.execute(sa.text("""
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'check_results'
                  AND column_name IN (
                      'reason_code', 'confidence_policy_version',
                      'confidence_details'
                  )
                ORDER BY column_name
            """))).all()

        assert observability_columns == [
            ("confidence_details", "NO"),
            ("confidence_policy_version", "NO"),
            ("reason_code", "NO"),
        ]

        alembic("upgrade", "head")
        await assert_database_at_repository_head(engine)
    finally:
        await engine.dispose()


async def test_assessment_foundation_is_isolated_from_later_remediation_schema():
    """The Phase 3.1 migration replays its own historical Assessment schema."""
    engine = create_async_engine(URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))

        alembic("upgrade", "20260808_02")
        async with engine.connect() as connection:
            historical_columns = set((await connection.execute(sa.text("""
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (
                    (table_name = 'student_submissions'
                     AND column_name = 'remediation_plan_id')
                    OR
                    (table_name = 'student_answers'
                     AND column_name = 'remediation_plan_item_id')
                  )
            """))).all())
            revision = await connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            )

        assert revision == "20260808_02"
        assert historical_columns == set()

        alembic("upgrade", "head")
        await assert_database_at_repository_head(engine)
        async with engine.connect() as connection:
            current_columns = set((await connection.execute(sa.text("""
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (
                    (table_name = 'student_submissions'
                     AND column_name = 'remediation_plan_id')
                    OR
                    (table_name = 'student_answers'
                     AND column_name = 'remediation_plan_item_id')
                  )
            """))).all())

        assert current_columns == {
            ("student_submissions", "remediation_plan_id"),
            ("student_answers", "remediation_plan_item_id"),
        }
    finally:
        await engine.dispose()


async def test_clean_online_upgrade_installs_c10a_vertical_schema():
    """A fresh online upgrade produces the sole expected head and vertical tables."""
    engine = create_async_engine(URL)
    expected = {
        "class_groups", "class_group_teachers", "classroom_audit_log",
        "class_notes", "student_notes", "remediation_plans",
        "remediation_plan_signals", "remediation_plan_items", "remediation_events",
        "student_submissions", "student_answers", "check_runs", "check_results",
        "check_findings", "model_runs", "checker_events",
    }
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))
        alembic("upgrade", "head")
        await assert_database_at_repository_head(engine)
        async with engine.connect() as connection:
            tables = set((await connection.execute(sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name = ANY(:names)"
            ), {"names": list(expected)})).scalars())
        assert tables == expected
    finally:
        await engine.dispose()



async def test_upgrade_from_supported_20260823_02_baseline_to_head():
    engine = create_async_engine(URL)
    owner_id = uuid4()
    session_id = uuid4()
    review_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))

        # Replay the canonical historical graph instead of stamping a partial,
        # hand-written approximation of the supported baseline.
        alembic("upgrade", "20260823_02")
        alembic("upgrade", "20260823_04")

        async with engine.begin() as connection:
            await connection.execute(
                sa.text("""INSERT INTO authoring_sessions
                    (id, owner_id, schema_version, policy_version, frozen_request,
                     request_fingerprint, frozen_allowlist)
                    VALUES (:id, :owner, 'v1', 'v1', '{}'::jsonb, :fingerprint,
                            '[]'::jsonb)"""),
                {"id": session_id, "owner": owner_id, "fingerprint": "a" * 64},
            )
            await connection.execute(
                sa.text("""INSERT INTO authoring_reviews
                    (id, session_id, owner_id, draft, version)
                    VALUES (:id, :session, :owner, '{"prompt":"legacy"}'::jsonb, 3)"""),
                {"id": review_id, "session": session_id, "owner": owner_id},
            )

        alembic("upgrade", "head")
        await assert_database_at_repository_head(engine)

        async with engine.connect() as connection:
            result = (await connection.execute(
                sa.text("""SELECT change_summary
                    FROM authoring_review_revisions
                    WHERE review_id = :review"""),
                {"review": review_id},
            )).scalar_one()

        assert result["source"] == "legacy_backfill"
        assert result["history_available"] is False
    finally:
        await engine.dispose()


async def test_account_revision_upgrades_downgrades_and_preserves_schema():
    engine = create_async_engine(URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))
        alembic("upgrade", "20260831_01")
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    sa.text("SELECT to_regclass('public.students')")
                )
                == "students"
            )
            assert (
                await connection.scalar(sa.text("SELECT to_regclass('public.users')"))
                is None
            )
        alembic("upgrade", "20260831_02")
        async with engine.begin() as connection:
            tables = set(
                (
                    await connection.execute(
                        sa.text(
                            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('users','user_roles','auth_sessions','student_user_links')"
                        )
                    )
                ).scalars()
            )
            assert tables == {
                "users",
                "user_roles",
                "auth_sessions",
                "student_user_links",
            }
            user_id = await connection.scalar(
                sa.text(
                    "INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES ('Admin','admin','Admin','opaque') RETURNING id"
                )
            )
            await connection.execute(
                sa.text("INSERT INTO user_roles(user_id,role) VALUES (:id,'admin')"),
                {"id": user_id},
            )
            indexes = set(
                (
                    await connection.execute(
                        sa.text(
                            "SELECT indexname FROM pg_indexes WHERE tablename='auth_sessions'"
                        )
                    )
                ).scalars()
            )
            assert {
                "uq_auth_sessions_token_hash",
                "ix_auth_sessions_user_expires",
                "ix_auth_sessions_active_expires",
            } <= indexes
        alembic("downgrade", "20260831_01")
        async with engine.connect() as connection:
            assert (
                await connection.scalar(sa.text("SELECT to_regclass('public.users')"))
                is None
            )
            assert (
                await connection.scalar(
                    sa.text("SELECT to_regclass('public.students')")
                )
                == "students"
            )
        alembic("upgrade", "head")
        await assert_database_at_repository_head(engine)
    finally:
        await engine.dispose()


async def test_j1f_resolution_revision_constraints_and_foreign_keys():
    """20260901_02 owns the five-table resolution shape and rejects invalid states."""
    engine = create_async_engine(URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))
        alembic("upgrade", "20260901_01")
        alembic("upgrade", "20260901_02")
        async with engine.connect() as connection:
            columns = (await connection.execute(sa.text("""
                SELECT table_name,column_name FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name IN ('subjects','grades','topics','subtopics','skills')
                  AND column_name IN ('resolved_by','resolved_at','resolution_reason','replacement_id')
            """))).all()
            assert len(columns) == 20
            foreign_keys = (await connection.execute(sa.text("""
                SELECT tc.table_name, kcu.column_name, ccu.table_name, rc.delete_rule
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name=ccu.constraint_name
                JOIN information_schema.referential_constraints rc ON tc.constraint_name=rc.constraint_name
                WHERE tc.constraint_type='FOREIGN KEY'
                  AND tc.table_name IN ('subjects','grades','topics','subtopics','skills')
                  AND kcu.column_name IN ('resolved_by','replacement_id')
            """))).all()
            assert len(foreign_keys) == 10
            assert all(row.delete_rule == "RESTRICT" for row in foreign_keys)
            assert all(row[2] == ("users" if row.column_name == "resolved_by" else row.table_name) for row in foreign_keys)
        async with engine.begin() as connection:
            actor = await connection.scalar(sa.text("INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES ('a','a','A','h') RETURNING id"))
            source = await connection.scalar(sa.text("INSERT INTO subjects(code,name,normalized_name,status,proposed_by) VALUES ('s','S','s','provisional',:a) RETURNING id"), {"a": actor})
        invalid = [
            ("UPDATE subjects SET resolved_by=:a,resolved_at=now() WHERE id=:s", {"a": actor, "s": source}),
            ("UPDATE subjects SET replacement_id=:s WHERE id=:s", {"s": source}),
            ("UPDATE subjects SET status='deprecated',resolved_by=:a,resolved_at=now(),replacement_id=:s WHERE id=:s", {"a": actor, "s": source}),
            ("UPDATE subjects SET status='deprecated',resolved_by=:a,resolved_at=now(),resolution_reason=:r WHERE id=:s", {"a": actor, "s": source, "r": "x" * 501}),
        ]
        for statement, params in invalid:
            with pytest.raises(sa.exc.IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(sa.text(statement), params)
        alembic("downgrade", "20260901_01")
        async with engine.connect() as connection:
            assert await connection.scalar(sa.text("SELECT count(*) FROM information_schema.columns WHERE table_name='subjects' AND column_name='resolved_by'")) == 0
        alembic("upgrade", "head")
        await assert_database_at_repository_head(engine)
    finally:
        await engine.dispose()
