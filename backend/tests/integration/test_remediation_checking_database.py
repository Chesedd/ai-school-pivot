"""C9C Checking target, fake-provider persistence, and replay DB contracts."""

import pytest
from sqlalchemy import text
from tests.integration.c10a_postgres import assert_constraints, rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_deterministic_and_model_rows_use_the_remediation_item_target():
    async with rolled_back_connection() as connection:
        await assert_constraints(connection,
            "ck_check_results_execution_item_xor",
            "ck_model_runs_execution_item_xor",
            "ck_checker_events_execution_item_at_most_one")
        columns = set((await connection.execute(text("""
          SELECT table_name,column_name FROM information_schema.columns WHERE
          table_name=ANY(ARRAY['check_results','model_runs','checker_events']) AND
          column_name IN ('assessment_item_id','remediation_plan_item_id')
        """))).all())
        assert len(columns) == 6


async def test_replayed_intake_has_database_idempotency_guards():
    async with rolled_back_connection() as connection:
        constraints = set((await connection.execute(text("""
          SELECT conname FROM pg_constraint WHERE conrelid='check_runs'::regclass AND contype='u'
        """))).scalars())
        assert {"uq_check_runs_submission_request", "uq_check_runs_submission_attempt"} <= constraints
