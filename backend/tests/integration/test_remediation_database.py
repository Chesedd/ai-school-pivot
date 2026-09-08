"""C8 remediation provenance, privacy, lifecycle, and ranking DB contracts."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.application.remediation import RemediationError
from app.infrastructure.remediation_repository import RemediationRepository
from tests.integration.c10a_postgres import assert_constraints, assert_tables, rolled_back_connection
from tests.integration.c9ab_fixtures import seed_execution_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_provenance_chain_and_selected_finding_task_constraints_exist():
    async with rolled_back_connection() as connection:
        await assert_tables(connection, "remediation_plans", "remediation_plan_signals", "remediation_plan_items", "remediation_events")
        foreign_keys = set((await connection.execute(text("""
          SELECT a.attname FROM pg_constraint c
          JOIN unnest(c.conkey) k(attnum) ON true
          JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum
          WHERE c.contype='f' AND c.conrelid='remediation_plans'::regclass
        """))).scalars())
        assert {"source_assignment_id","source_assignment_participant_id","source_submission_id","source_check_run_id","student_id"} <= foreign_keys
        await assert_constraints(connection, "uq_remediation_plans_owner_creation_key")


async def test_lifecycle_cas_privacy_and_candidate_inputs_are_persisted():
    async with rolled_back_connection() as connection:
        columns = set((await connection.execute(text("""
          SELECT column_name FROM information_schema.columns WHERE table_name='remediation_plans'
        """))).scalars())
        assert {"owner_user_id","status","updated_at","assigned_at","cancelled_at","review_acknowledged_at","class_group_id"} <= columns
        links = set((await connection.execute(text("""
          SELECT table_name FROM information_schema.tables WHERE table_schema='public'
          AND table_name=ANY(ARRAY['task_skill_links','task_error_links','task_versions','check_findings'])
        """))).scalars())
        assert links == {"task_skill_links","task_error_links","task_versions","check_findings"}


async def test_source_substitution_is_rejected_as_one_coherent_chain():
    """Individually valid foreign IDs must not compose a valid remediation source."""
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection)
        await connection.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id) VALUES (:group_a,:owner)"), ids)
        session = AsyncSession(bind=connection, expire_on_commit=False)
        repository = RemediationRepository(session)
        valid = (ids["assignment_0"], ids["student_0"], ids["source_submission_0"],
                 ids["source_run_0"], ids["participant_0"])
        row = await repository.source(ids["owner"], False, *valid)
        assert (row[0].id, row[1].id, row[2].id, row[3].id, row[4].id) == valid
        substitutions = (
            (ids["assignment_0"], ids["student_0"], ids["source_submission_0"], ids["source_run_0"], ids["participant_1"]),
            (ids["assignment_0"], ids["student_0"], ids["source_submission_1"], ids["source_run_0"], ids["participant_0"]),
            (ids["assignment_0"], ids["student_0"], ids["source_submission_0"], ids["source_run_1"], ids["participant_0"]),
            (ids["assignment_0"], ids["student_1"], ids["source_submission_0"], ids["source_run_0"], ids["participant_0"]),
        )
        for candidate in substitutions:
            with pytest.raises(RemediationError, match="remediation_source_not_found") as error:
                await repository.source(ids["owner"], False, *candidate)
            assert error.value.status == 404
        await session.close()


async def test_cancel_and_owner_privacy_are_idempotent_and_preserve_source():
    async with rolled_back_connection() as connection:
        ids = await seed_execution_world(connection)
        await connection.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id) VALUES (:group_a,:owner)"), ids)
        session = AsyncSession(bind=connection, expire_on_commit=False)
        repository = RemediationRepository(session)
        before = await connection.scalar(text("SELECT count(*) FROM assignment_participants WHERE id=:participant_0"), ids)
        cancelled = await repository.cancel(ids["plan_0"], ids["owner"], False)
        replay = await repository.cancel(ids["plan_0"], ids["owner"], False)
        assert cancelled["status"] == replay["status"] == "cancelled"
        assert cancelled["cancelled_at"] == replay["cancelled_at"]
        assert await connection.scalar(text("SELECT count(*) FROM remediation_events WHERE remediation_plan_id=:plan_0 AND event_type='plan_cancelled'"), ids) == 1
        assert await connection.scalar(text("SELECT count(*) FROM assignment_participants WHERE id=:participant_0"), ids) == before
        with pytest.raises(RemediationError, match="remediation_not_found"):
            await repository.owned(ids["plan_0"], ids["user_1"], False)
        await session.close()
