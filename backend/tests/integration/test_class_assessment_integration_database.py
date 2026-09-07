"""C6/C9A Assessment snapshot and generalized-schema database contracts."""

import pytest
from sqlalchemy import text
from tests.integration.c10a_postgres import assert_constraints, rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_assessment_reuse_roster_snapshots_and_execution_columns_are_installed():
    async with rolled_back_connection() as connection:
        await assert_constraints(
            connection,
            "uq_assignment_participants_assignment_student",
            "ck_student_submissions_execution_target_xor",
            "ck_student_answers_execution_item_xor",
        )
        columns = set((await connection.execute(text("""
          SELECT table_name,column_name FROM information_schema.columns
          WHERE table_schema='public' AND
            ((table_name='assignments' AND column_name IN ('assessment_id','class_group_id')) OR
             (table_name='assignment_participants' AND column_name IN ('assignment_id','student_id')) OR
             (table_name='student_submissions' AND column_name IN ('assignment_participant_id','remediation_plan_id')) OR
             (table_name='student_answers' AND column_name IN ('assessment_item_id','remediation_plan_item_id')))
        """))).all())
        assert len(columns) == 8


async def test_assignment_participants_are_historical_rows_not_live_roster_views():
    async with rolled_back_connection() as connection:
        definition = await connection.scalar(text("""
          SELECT pg_get_constraintdef(oid) FROM pg_constraint
          WHERE conname='fk_assignment_participants_student_id_students'
        """))
        assert "REFERENCES students(id) ON UPDATE RESTRICT ON DELETE RESTRICT" in definition
