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
from sqlalchemy.dialects import postgresql
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


def supported_baseline_metadata() -> sa.MetaData:
    """Return the affected portion of the schema as it was at 20260823_02."""
    metadata = sa.MetaData()
    # Canonical curriculum tables existed at this baseline.  Keep their
    # historical shape here so forward migrations, rather than the synthetic
    # fixture, remain the sole owner of later lifecycle columns.
    subjects = sa.Table(
        "subjects",
        metadata,
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("code", name="uq_subjects_code"),
    )
    grades = sa.Table(
        "grades",
        metadata,
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("number", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("number", name="uq_grades_number"),
        sa.CheckConstraint("number BETWEEN 1 AND 11", name="ck_grades_number_range"),
    )
    topics = sa.Table(
        "topics",
        metadata,
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "subject_id",
            sa.Uuid(),
            sa.ForeignKey(subjects.c.id, ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "grade_id",
            sa.Uuid(),
            sa.ForeignKey(grades.c.id, ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "subject_id", "grade_id", "code", name="uq_topics_subject_grade_code"
        ),
    )
    subtopics = sa.Table(
        "subtopics",
        metadata,
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "topic_id",
            sa.Uuid(),
            sa.ForeignKey(topics.c.id, ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("topic_id", "code", name="uq_subtopics_topic_code"),
    )
    sa.Table(
        "skills",
        metadata,
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "subtopic_id",
            sa.Uuid(),
            sa.ForeignKey(subtopics.c.id, ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("subtopic_id", "code", name="uq_skills_subtopic_code"),
    )
    # These tables already existed at the supported revision and are required
    # by the later account migration's student_user_links foreign key.
    from app.infrastructure.assessment_models import ClassGroup, Student
    ClassGroup.__table__.to_metadata(metadata)
    Student.__table__.to_metadata(metadata)
    session_status = postgresql.ENUM(
        "draft", "generating", "ready", "confirmed", "rejected", "expired",
        name="authoring_session_status",
    )
    attempt_status = postgresql.ENUM(
        "pending", "running", "succeeded", "invalid_output", "failed_retryable",
        "failed_terminal", name="authoring_attempt_status",
    )
    authoring_role = postgresql.ENUM("generator", "solver", name="authoring_role")

    sessions = sa.Table(
        "authoring_sessions", metadata,
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(128), nullable=False),
        sa.Column("frozen_request", postgresql.JSONB(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("frozen_allowlist", postgresql.JSONB(), nullable=False),
        sa.Column("status", session_status, server_default=sa.text("'draft'::authoring_session_status"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("generator_route", postgresql.JSONB()),
        sa.Column("solver_route", postgresql.JSONB()),
        sa.Column("generated_draft", postgresql.JSONB()),
        sa.Column("solver_result", postgresql.JSONB()),
        sa.Column("validation_result", postgresql.JSONB()),
        sa.Column("pipeline_identity", sa.String(64)),
        sa.Column("semantic_status", sa.String(64)),
        sa.Column("generator_attempt_id", sa.Uuid()),
        sa.Column("solver_attempt_id", sa.Uuid()),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_authoring_sessions_fingerprint"),
        sa.CheckConstraint("char_length(schema_version) BETWEEN 1 AND 64 AND char_length(policy_version) BETWEEN 1 AND 128", name="ck_authoring_sessions_versions"),
        sa.CheckConstraint("row_version > 0", name="ck_authoring_sessions_revision"),
        sa.CheckConstraint("pipeline_identity IS NULL OR pipeline_identity ~ '^[0-9a-f]{64}$'", name="ck_authoring_sessions_pipeline_identity"),
    )
    sa.Index("ix_authoring_sessions_owner_status", sessions.c.owner_id, sessions.c.status)
    sa.Index("ix_authoring_sessions_status_created", sessions.c.status, sessions.c.created_at)

    attempts = sa.Table(
        "authoring_provider_attempts", metadata,
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", authoring_role, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("provider_id", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("settings_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("prompt_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("timeout_ms", sa.Integer(), nullable=False),
        sa.Column("status", attempt_status, server_default=sa.text("'pending'::authoring_attempt_status"), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("provider_request_id", sa.String(256)),
        sa.Column("response_hash", sa.String(64)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cached_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_amount", sa.Numeric(18, 8)),
        sa.Column("currency", sa.String(3)),
        sa.Column("pricing_version", sa.String(128)),
        sa.Column("pricing_source", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("cache_read_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["authoring_sessions.id"], name="fk_authoring_attempts_session", ondelete="RESTRICT"),
        sa.UniqueConstraint("session_id", "idempotency_key", name="uq_authoring_attempts_session_key"),
        sa.UniqueConstraint("session_id", "role", "attempt_number", name="uq_authoring_attempts_number"),
        sa.CheckConstraint("attempt_number > 0 AND timeout_ms BETWEEN 1 AND 120000 AND input_tokens BETWEEN 0 AND 10000000 AND output_tokens BETWEEN 0 AND 10000000 AND cached_tokens BETWEEN 0 AND 10000000", name="ck_authoring_attempts_numbers"),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$' AND (response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$')", name="ck_authoring_attempts_hashes"),
        sa.CheckConstraint("cost_amount IS NULL OR cost_amount >= 0", name="ck_authoring_attempts_cost"),
        sa.CheckConstraint("provider_id=btrim(provider_id) AND char_length(provider_id) BETWEEN 1 AND 128 AND model_id=btrim(model_id) AND char_length(model_id) BETWEEN 1 AND 128 AND idempotency_key=btrim(idempotency_key) AND char_length(idempotency_key) BETWEEN 1 AND 128", name="ck_authoring_attempts_ids"),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_authoring_attempts_latency"),
        sa.CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_authoring_attempts_time"),
        sa.CheckConstraint("(cost_amount IS NULL AND currency IS NULL AND pricing_version IS NULL AND pricing_source IS NULL) OR (cost_amount IS NOT NULL AND currency ~ '^[A-Z]{3}$' AND char_length(pricing_version) BETWEEN 1 AND 128 AND char_length(pricing_source) BETWEEN 1 AND 128)", name="ck_authoring_attempts_cost_metadata"),
        sa.CheckConstraint("(status='pending' AND started_at IS NULL AND finished_at IS NULL) OR (status='running' AND started_at IS NOT NULL AND finished_at IS NULL) OR (status IN ('succeeded','invalid_output','failed_retryable','failed_terminal') AND started_at IS NOT NULL AND finished_at IS NOT NULL)", name="ck_authoring_attempts_lifecycle"),
        sa.CheckConstraint("(status IN ('failed_retryable','failed_terminal') AND failure_code IS NOT NULL) OR (status NOT IN ('failed_retryable','failed_terminal') AND failure_code IS NULL)", name="ck_authoring_attempts_failure"),
        sa.CheckConstraint("cache_read_tokens BETWEEN 0 AND 10000000 AND cache_write_tokens BETWEEN 0 AND 10000000", name="ck_authoring_attempts_cache_dimensions"),
    )
    sa.Index("ix_authoring_attempts_session_status", attempts.c.session_id, attempts.c.status)
    sa.Index("ix_authoring_attempts_claim", attempts.c.status, attempts.c.created_at)
    sa.Index("ix_authoring_attempts_provider_request", attempts.c.provider_request_id)
    sessions.append_constraint(sa.ForeignKeyConstraint([sessions.c.generator_attempt_id], [attempts.c.id], name="fk_authoring_sessions_generator_attempt", ondelete="RESTRICT", use_alter=True))
    sessions.append_constraint(sa.ForeignKeyConstraint([sessions.c.solver_attempt_id], [attempts.c.id], name="fk_authoring_sessions_solver_attempt", ondelete="RESTRICT", use_alter=True))
    return metadata


async def test_upgrade_from_supported_20260823_02_baseline_to_head():
    engine = create_async_engine(URL)
    owner_id = uuid4()
    session_id = uuid4()
    review_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))
            await connection.run_sync(supported_baseline_metadata().create_all)

        alembic("stamp", "20260823_02")
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
