"""Final Classroom business/security smoke against disposable PostgreSQL.

Fixture SQL only bootstraps actors and an already checked source graph.  Product
operations (student execution and result projections) go through application
services; the assertions deliberately span ownership, history and idempotency.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.classroom_results import (ClassroomAssessmentResultsService,
    ClassroomResultsError, ResultActor)
from app.application.remediation import RemediationError
from app.application.remediation_execution import RemediationExecutionService
from app.application.remediation_results import RemediationResultsService
from app.infrastructure.classroom_results_repository import SQLAlchemyClassroomResultsReadRepository
from app.infrastructure.remediation_repository import RemediationRepository
from tests.integration.c10a_postgres import rolled_back_connection
from tests.integration.c9ab_fixtures import seed_execution_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_full_classroom_history_execution_and_idor_smoke():
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection, plans=2)
        await connection.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id) VALUES (:group_a,:owner)"), ids)
        factory = async_sessionmaker(bind=connection, expire_on_commit=False)
        execution = RemediationExecutionService(factory)

        # Student A's replay-safe lifecycle creates exactly one attempt.
        first, first_status = await execution.start(ids["plan_0"], ids["student_0"])
        replay, replay_status = await execution.start(ids["plan_0"], ids["student_0"])
        assert (first_status, replay_status, first["submission_id"]) == (201, 200, replay["submission_id"])
        await execution.save_answer(ids["plan_0"], ids["plan_item_0_0"], ids["student_0"], "42")
        await execution.submit(ids["plan_0"], ids["student_0"])
        await execution.submit(ids["plan_0"], ids["student_0"])
        assert await connection.scalar(text("SELECT count(*) FROM student_submissions WHERE remediation_plan_id=:plan_0"), ids) == 1

        # Student B gets the same non-enumerating failure for every known-ID mutation.
        for operation in (
            lambda: execution.get_execution(ids["plan_0"], ids["student_1"]),
            lambda: execution.start(ids["plan_0"], ids["student_1"]),
            lambda: execution.save_answer(ids["plan_0"], ids["plan_item_0_0"], ids["student_1"], "stolen"),
            lambda: execution.delete_answer(ids["plan_0"], ids["plan_item_0_0"], ids["student_1"]),
            lambda: execution.submit(ids["plan_0"], ids["student_1"]),
        ):
            with pytest.raises(RemediationError, match="remediation_not_found"):
                await operation()

        # Moving after assignment rewrites neither source history nor entitlement.
        await connection.execute(text("UPDATE students SET class_group_id=:group_b WHERE id=:student_0"), ids)
        assert (await execution.get_execution(ids["plan_0"], ids["student_0"]))["submission_id"] == first["submission_id"]
        assert await connection.scalar(text("SELECT count(*) FROM assignment_participants WHERE id=:participant_0"), ids) == 1
        assert await connection.scalar(text("SELECT class_group_id FROM remediation_plans WHERE id=:plan_0"), ids) == ids["group_a"]
        assert await connection.scalar(text("SELECT count(*) FROM remediation_plans")) == 2

        # Teacher result boundaries enforce Assessment and remediation ownership.
        session = AsyncSession(bind=connection, expire_on_commit=False)
        c7 = ClassroomAssessmentResultsService(SQLAlchemyClassroomResultsReadRepository(session))
        own = await c7.assignment_results(ids["assignment_0"], ResultActor(ids["owner"], False), 0, 20)
        assert own["items"][0]["is_current_class_member"] is False
        with pytest.raises(ClassroomResultsError):
            await c7.assignment_results(ids["assignment_0"], ResultActor(ids["user_1"], False), 0, 20)
        with pytest.raises(RemediationError, match="remediation_not_found"):
            await RemediationRepository(session).owned(ids["plan_0"], ids["user_1"], False)
        with pytest.raises(RemediationError, match="remediation_not_found"):
            await RemediationResultsService(session).teacher_result(ids["plan_0"], ids["user_1"], False)
        await session.close()
