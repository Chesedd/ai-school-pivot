"""C7 result-projection persistence acceptance checks."""

import pytest
from sqlalchemy import text
from app.application.classroom_results import check_status, score_totals
from tests.integration.c10a_postgres import rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_result_tables_support_all_projected_states_and_latest_run_order():
    async with rolled_back_connection() as connection:
        enums = set((await connection.execute(text("""
          SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid
          WHERE t.typname='checking_run_status'
        """))).scalars())
        assert {"pending","running","completed","completed_with_review_required","failed_terminal"} <= enums
        assert check_status(False, None) == "not_started"
        assert check_status(True, "running") == "checking"
        assert check_status(True, "completed") == "checked"
        assert check_status(True, "completed_with_review_required") == "review_required"
        assert check_status(True, "failed_terminal") == "check_failed"


async def test_null_score_poisoning_is_retained_by_projection_contract():
    async with rolled_back_connection() as connection:
        nullable = await connection.scalar(text("""
          SELECT is_nullable FROM information_schema.columns
          WHERE table_name='check_results' AND column_name='score_suggested'
        """))
        assert nullable == "YES"
        row = type("Result", (), {"score_suggested": None, "max_score": 10})()
        assert score_totals([row]) == (None, None, None)
