"""Focused PostgreSQL migration regression coverage."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
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


def alembic(*arguments: str) -> str:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = URL
    completed = subprocess.run(
        ["alembic", *arguments],
        cwd=BACKEND,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


async def test_clean_database_upgrades_to_head_with_observability_columns():
    """The foundation revision must not leak columns from live ORM metadata."""
    engine = create_async_engine(URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))

        alembic("upgrade", "head")

        async with engine.connect() as connection:
            columns = (await connection.execute(sa.text("""
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

        assert columns == [
            ("confidence_details", "NO"),
            ("confidence_policy_version", "NO"),
            ("reason_code", "NO"),
        ]
        assert "20260824_02 (head)" in alembic("current")
    finally:
        await engine.dispose()


def supported_baseline_metadata() -> sa.MetaData:
    """Return the affected portion of the schema as it was at 20260823_02."""
    metadata = sa.MetaData()
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
        assert "20260824_02 (head)" in alembic("current")

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
