"""Real-PostgreSQL contract checks for the shared execution target schema."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.c10a_postgres import require_disposable_postgres

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]


def _async_url(url: str) -> str:
    return url if "+asyncpg" in url else url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_execution_target_constraints_and_partial_uniqueness_are_installed():
    """Verify the migrated database, rather than merely ORM declarations."""
    engine = create_async_engine(require_disposable_postgres())
    expected_checks = {
        "ck_student_submissions_execution_target_xor": "num_nonnulls(assignment_participant_id, remediation_plan_id) = 1",
        "ck_student_answers_execution_item_xor": "num_nonnulls(assessment_item_id, remediation_plan_item_id) = 1",
        "ck_check_results_execution_item_xor": "num_nonnulls(assessment_item_id, remediation_plan_item_id) = 1",
        "ck_model_runs_execution_item_xor": "num_nonnulls(assessment_item_id, remediation_plan_item_id) = 1",
        "ck_checker_events_execution_item_at_most_one": "num_nonnulls(assessment_item_id, remediation_plan_item_id) <= 1",
    }
    expected_indexes = {
        "uq_student_submissions_participant_attempt",
        "uq_student_submissions_remediation_attempt",
        "uq_student_answers_submission_item",
        "uq_student_answers_submission_remediation_item",
        "uq_check_results_run_item",
        "uq_check_results_run_remediation_item",
        "uq_model_runs_attempt",
        "uq_model_runs_remediation_attempt",
    }
    async with engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        checks = dict((await connection.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = ANY(:names)"
        ), {"names": list(expected_checks)})).all())
        indexes = dict((await connection.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY(:names)"
        ), {"names": list(expected_indexes)})).all())
        columns = set((await connection.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND column_name LIKE 'remediation_plan%id'"
        ))).all())
    await engine.dispose()

    assert revision == "20260907_04"
    assert checks.keys() == expected_checks.keys()
    for name, expression in expected_checks.items():
        assert expression in checks[name]
    assert indexes.keys() == expected_indexes
    assert all("UNIQUE INDEX" in definition and " WHERE " in definition for definition in indexes.values())
    assert {
        ("student_submissions", "remediation_plan_id"),
        ("student_answers", "remediation_plan_item_id"),
        ("check_results", "remediation_plan_item_id"),
        ("model_runs", "remediation_plan_item_id"),
        ("checker_events", "remediation_plan_item_id"),
    } <= columns
