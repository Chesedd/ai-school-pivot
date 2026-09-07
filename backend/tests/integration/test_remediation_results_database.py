"""C9D teacher and safe-student result projection PostgreSQL acceptance."""

import pytest
from sqlalchemy import text
from app.application.remediation_results import execution_state
from tests.integration.c10a_postgres import assert_tables, rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_teacher_result_graph_and_student_safe_projection_sources_exist():
    async with rolled_back_connection() as connection:
        await assert_tables(connection, "remediation_plans", "student_submissions", "student_answers", "check_runs", "check_results", "check_findings")
        sensitive = set((await connection.execute(text("""
          SELECT column_name FROM information_schema.columns WHERE table_name='check_results'
          AND column_name=ANY(ARRAY['teacher_summary','model_limitations','score_suggested','summary'])
        """))).scalars())
        assert sensitive == {"teacher_summary","model_limitations","score_suggested","summary"}


async def test_latest_failed_run_never_falls_back_to_completed_result():
    async with rolled_back_connection() as connection:
        statuses = set((await connection.execute(text("""
          SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid
          WHERE t.typname='checking_run_status'
        """))).scalars())
        assert {"completed", "failed_terminal"} <= statuses
        plan = type("Plan", (), {"status":"assigned", "due_at":None})()
        submission = type("Submission", (), {"status":"submitted"})()
        latest = type("Run", (), {"status":"failed_terminal"})()
        assert execution_state(plan, submission, latest, None) == "check_failed"
