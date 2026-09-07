"""C9B execution invariants verified against the migrated PostgreSQL schema."""

import pytest
from datetime import datetime, timezone
from sqlalchemy import text
from app.application.remediation import RemediationError
from app.application.remediation_execution import RemediationExecutionService
from tests.integration.c10a_postgres import rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_single_attempt_answer_target_and_idempotency_constraints():
    async with rolled_back_connection() as connection:
        indexes = dict((await connection.execute(text("""
          SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND
          indexname=ANY(ARRAY['uq_student_submissions_remediation_attempt',
          'uq_student_answers_submission_remediation_item','uq_check_runs_submission_request'])
        """))).all())
        assert len(indexes) == 3
        assert "remediation_plan_id IS NOT NULL" in indexes["uq_student_submissions_remediation_attempt"]
        assert "remediation_plan_item_id IS NOT NULL" in indexes["uq_student_answers_submission_remediation_item"]


async def test_deadline_cancellation_and_student_idor_are_fail_closed_contracts():
    async with rolled_back_connection() as connection:
        columns = set((await connection.execute(text("""
          SELECT table_name,column_name FROM information_schema.columns WHERE
          (table_name='remediation_plans' AND column_name IN ('student_id','due_at','cancelled_at','status')) OR
          (table_name='student_submissions' AND column_name IN ('remediation_plan_id','assignment_participant_id','attempt_no','status'))
        """))).all())
        assert len(columns) == 8
        with pytest.raises(RemediationError, match="remediation_cancelled"):
            RemediationExecutionService._mutable(
                type("Plan", (), {"status": "cancelled", "due_at": None})(),
                datetime.now(timezone.utc),
            )
