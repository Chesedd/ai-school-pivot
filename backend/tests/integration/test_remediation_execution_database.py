"""Executable C9B remediation scenarios against disposable PostgreSQL."""
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.remediation import RemediationError
from app.application.remediation_execution import RemediationExecutionService
from app.application.principal import Principal
from app.infrastructure.assessment_models import StudentAnswer, StudentSubmission
from tests.integration.c10a_postgres import rolled_back_connection
from tests.integration.c10a_postgres import require_disposable_postgres
from tests.integration.c9ab_fixtures import seed_execution_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _error(code):
    return pytest.raises(RemediationError, match=code)


@pytest.mark.parametrize("role", ["teacher", "admin"])
async def test_privileged_accounts_cannot_execute_without_student_identity(role):
    """Execution routes cannot turn a capability into student impersonation."""
    os.environ.setdefault("DATABASE_URL", require_disposable_postgres())
    from app.presentation.auth_dependencies import require_student_identity

    principal = Principal(uuid4(), role, role, frozenset({role}), frozenset(), None)
    with pytest.raises(HTTPException) as exc:
        require_student_identity(principal)
    assert (exc.value.status_code, exc.value.detail) == (403, "student_identity_required")


async def _service_world(connection):
    ids = await seed_execution_world(connection)
    factory = async_sessionmaker(bind=connection, expire_on_commit=False,
                                 join_transaction_mode="create_savepoint")
    return ids, RemediationExecutionService(factory), factory


async def test_full_start_save_update_delete_resume_submit_happy_path():
    async with rolled_back_connection() as connection:
        ids, service, factory = await _service_world(connection)
        plan, student = ids["plan_0"], ids["student_0"]
        item_1, item_2 = ids["plan_item_0_0"], ids["plan_item_0_1"]
        initial = await service.get_execution(plan, student)
        assert initial["execution_status"] == "not_started"
        started, status = await service.start(plan, student)
        assert status == 201
        submission_id = started["submission_id"]
        repeated, status = await service.start(plan, student)
        assert (status, repeated["submission_id"], repeated["attempt_no"]) == (200, submission_id, 1)

        created, status = await service.save_answer(plan, item_1, student, "first")
        assert (status, created["raw_answer"]) == (201, "first")
        await service.save_answer(plan, item_2, student, "second")
        updated, status = await service.save_answer(plan, item_1, student, "updated")
        assert (status, updated["raw_answer"]) == (200, "updated")
        await service.delete_answer(plan, item_2, student)

        # A new service instance proves resume is database-backed, not in-memory.
        resumed = await RemediationExecutionService(factory).get_execution(plan, student)
        assert (resumed["submission_id"], resumed["attempt_no"]) == (submission_id, 1)
        assert [i["current_raw_answer"] for i in resumed["items"]] == ["updated", None]
        submitted = await service.submit(plan, student)
        assert submitted["submission_status"] == "submitted"
        again = await service.submit(plan, student)
        assert again["submission_id"] == submission_id

        async with factory() as session:
            submissions = list(await session.scalars(select(StudentSubmission).where(
                StudentSubmission.remediation_plan_id == plan)))
            answers = list(await session.scalars(select(StudentAnswer).where(
                StudentAnswer.submission_id == submission_id)))
        assert len(submissions) == 1
        assert submissions[0].assignment_participant_id is None
        assert (submissions[0].attempt_no, submissions[0].status) == (1, "submitted")
        assert submissions[0].submitted_at is not None
        assert len(answers) == 1
        assert answers[0].assessment_item_id is None
        assert (answers[0].remediation_plan_item_id, answers[0].raw_answer) == (item_1, "updated")
        with _error("remediation_submission_immutable"):
            await service.save_answer(plan, item_1, student, "forbidden")
        with _error("remediation_submission_immutable"):
            await service.delete_answer(plan, item_1, student)
        resumed = await service.get_execution(plan, student)
        assert resumed["items"][0]["current_raw_answer"] == "updated"


async def test_student_idor_matrix_and_foreign_item_are_fail_closed():
    async with rolled_back_connection() as connection:
        ids, service, factory = await _service_world(connection)
        plan_a, student_b = ids["plan_0"], ids["student_1"]
        own_item, foreign_item = ids["plan_item_0_0"], ids["plan_item_1_0"]
        operations = (
            lambda: service.get_execution(plan_a, student_b),
            lambda: service.start(plan_a, student_b),
            lambda: service.save_answer(plan_a, own_item, student_b, "stolen"),
            lambda: service.delete_answer(plan_a, own_item, student_b),
            lambda: service.submit(plan_a, student_b),
        )
        for operation in operations:
            with _error("remediation_not_found"):
                await operation()
        assert await connection.scalar(text("SELECT count(*) FROM student_submissions WHERE remediation_plan_id=:p"), {"p": plan_a}) == 0

        await service.start(plan_a, ids["student_0"])
        with _error("remediation_item_not_found"):
            await service.save_answer(plan_a, foreign_item, ids["student_0"], "foreign")
        with _error("remediation_item_not_found"):
            await service.delete_answer(plan_a, foreign_item, ids["student_0"])
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(StudentAnswer).where(
                StudentAnswer.remediation_plan_item_id == foreign_item)) == 0


async def test_deadline_read_and_every_mutation_are_fail_closed():
    async with rolled_back_connection() as connection:
        ids, service, _ = await _service_world(connection)
        plan, student, item = ids["plan_0"], ids["student_0"], ids["plan_item_0_0"]
        # Seed a legitimate draft while mutable, then expire it so save/delete/submit apply.
        await service.start(plan, student)
        await service.save_answer(plan, item, student, "historical")
        await connection.execute(text("UPDATE remediation_plans SET due_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": plan})
        assert (await service.get_execution(plan, student))["execution_status"] == "expired"
        for operation in (lambda: service.start(plan, student),
                          lambda: service.save_answer(plan, item, student, "late"),
                          lambda: service.delete_answer(plan, item, student),
                          lambda: service.submit(plan, student)):
            with _error("remediation_expired"):
                await operation()
        assert (await service.get_execution(plan, student))["items"][0]["current_raw_answer"] == "historical"


async def test_cancellation_preserves_history_and_rejects_mutations():
    async with rolled_back_connection() as connection:
        ids, service, _ = await _service_world(connection)
        plan, student, item = ids["plan_0"], ids["student_0"], ids["plan_item_0_0"]
        started, _ = await service.start(plan, student)
        await service.save_answer(plan, item, student, "historical")
        await connection.execute(text("UPDATE remediation_plans SET status='cancelled',cancelled_at=clock_timestamp() WHERE id=:id"), {"id": plan})
        assert (await service.get_execution(plan, student))["execution_status"] == "cancelled"
        for operation in (lambda: service.save_answer(plan, item, student, "changed"),
                          lambda: service.delete_answer(plan, item, student),
                          lambda: service.submit(plan, student)):
            with _error("remediation_cancelled"):
                await operation()
        row = (await connection.execute(text("SELECT s.id,a.raw_answer FROM student_submissions s JOIN student_answers a ON a.submission_id=s.id WHERE s.remediation_plan_id=:p"), {"p": plan})).one()
        assert (row.id, row.raw_answer) == (started["submission_id"], "historical")


async def test_student_move_after_assignment_does_not_revoke_plan_entitlement():
    async with rolled_back_connection() as connection:
        ids, service, _ = await _service_world(connection)
        plan, student, item = ids["plan_0"], ids["student_0"], ids["plan_item_0_0"]
        await connection.execute(text("UPDATE students SET class_group_id=:new WHERE id=:student"),
                                 {"new": ids["group_b"], "student": student})
        assert (await service.get_execution(plan, student))["execution_status"] == "not_started"
        started, _ = await service.start(plan, student)
        await service.save_answer(plan, item, student, "after move")
        submitted = await service.submit(plan, student)
        assert submitted["submission_id"] == started["submission_id"]
        assert submitted["submission_status"] == "submitted"
