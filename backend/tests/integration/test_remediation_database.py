"""C8 remediation provenance, privacy, lifecycle, and ranking DB contracts."""

import pytest
from sqlalchemy import text
from tests.integration.c10a_postgres import assert_constraints, assert_tables, rolled_back_connection

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
