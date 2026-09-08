"""C7 result-projection persistence acceptance checks."""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.application.classroom_results import check_status, score_totals
from app.application.classroom_results import (ClassroomAssessmentResultsService,
    ClassroomResultsError, ResultActor)
from app.infrastructure.classroom_results_repository import SQLAlchemyClassroomResultsReadRepository
from tests.integration.c10a_postgres import rolled_back_connection
from tests.integration.c9ab_fixtures import seed_execution_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_result_tables_support_all_projected_states_and_latest_run_order():
    async with rolled_back_connection() as connection:
        enums = set((await connection.execute(text("""
          SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid
          WHERE t.typname='checking_run_status'
        """))).scalars())
        assert {"pending","running","completed","completed_with_review_required","failed_terminal"} <= enums
        assert check_status(False, None) == "not_submitted"
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
        assert score_totals([row]) == (None, Decimal("10"), None)


async def test_persisted_projection_uses_historical_participant_and_owner_scope():
    """C7 reads snapshots after a move and never grants a co-teacher ownership."""
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection, plans=1)
        await connection.execute(text("""
          INSERT INTO class_group_teachers(class_group_id,teacher_user_id)
          VALUES (:group_a,:owner)
        """), ids)
        session = AsyncSession(bind=connection, expire_on_commit=False)
        service = ClassroomAssessmentResultsService(SQLAlchemyClassroomResultsReadRepository(session))
        actor = ResultActor(ids["owner"], False)
        before = await service.assignment_results(ids["assignment_0"], actor, 0, 50)
        assert before["participant_count"] == 1
        assert before["items"][0]["check_status"] == "checked"
        assert before["items"][0]["is_current_class_member"] is True

        await connection.execute(text("UPDATE students SET class_group_id=:group_b WHERE id=:student_0"), ids)
        moved = await service.student_result(ids["assignment_0"], ids["student_0"], actor)
        assert moved["is_current_class_member"] is False
        assert moved["class_group_id"] == ids["group_a"]
        assert "destination_class_group_id" not in moved

        stranger = ResultActor(ids["user_0"], False)
        with pytest.raises(ClassroomResultsError, match="недоступны") as denied:
            await service.assignment_results(ids["assignment_0"], stranger, 0, 50)
        assert denied.value.code == "assignment_not_found"
        await session.close()


async def test_latest_submitted_attempt_and_latest_check_run_are_independent_of_draft():
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection, plans=1)
        await connection.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id) VALUES (:group_a,:owner)"), ids)
        await connection.execute(text("""
          INSERT INTO student_submissions(id,assignment_participant_id,attempt_no,status)
          VALUES (gen_random_uuid(),:participant_0,2,'draft')
        """), ids)
        await connection.execute(text("""
          UPDATE check_runs SET status='failed_terminal',attempt_no=2,
            failure_code='terminal' WHERE id=:source_run_0
        """), ids)
        session = AsyncSession(bind=connection, expire_on_commit=False)
        result = await ClassroomAssessmentResultsService(
            SQLAlchemyClassroomResultsReadRepository(session)).student_result(
                ids["assignment_0"], ids["student_0"], ResultActor(ids["owner"], False))
        assert (result["current_attempt_no"], result["current_attempt_status"]) == (2, "draft")
        assert result["selected_attempt_no"] == 1
        assert result["latest_check_run_status"] == "failed_terminal"
        assert result["check_status"] == "check_failed"
        await session.close()
