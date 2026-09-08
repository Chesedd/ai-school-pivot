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


async def test_occurrences_reuse_assessment_and_snapshot_roster_without_backfill():
    """Persisted C6 invariant: occurrences have identity; participants are snapshots."""
    from uuid import uuid4

    async with rolled_back_connection() as connection:
        ids = {name: uuid4() for name in
               ("owner", "group_a", "group_b", "assessment", "a1", "a2", "a3", "s1", "s2", "s3", "s4")}
        await connection.execute(text("""
          INSERT INTO users(id,login,normalized_login,display_name,password_hash)
            VALUES (:owner,'c10d3-owner','c10d3-owner','Owner','hash');
          INSERT INTO class_groups(id,name,created_by) VALUES (:group_a,'7A',:owner),(:group_b,'7B',:owner);
          INSERT INTO assessments(id,title,status,created_by,published_at,published_by)
            VALUES (:assessment,'Reusable','published',:owner,clock_timestamp(),:owner);
          INSERT INTO students(id,class_group_id,display_name) VALUES
            (:s1,:group_a,'A'),(:s2,:group_a,'B'),(:s3,:group_a,'C');
          INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by) VALUES
            (:a1,:assessment,:group_a,clock_timestamp(),clock_timestamp()+interval '1 day',:owner);
          INSERT INTO assignment_participants(assignment_id,student_id) SELECT :a1,id FROM students
            WHERE class_group_id=:group_a AND archived_at IS NULL;
          UPDATE students SET class_group_id=:group_b WHERE id=:s2;
          UPDATE students SET archived_at=clock_timestamp() WHERE id=:s3;
          INSERT INTO students(id,class_group_id,display_name) VALUES (:s4,:group_a,'D');
          INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by) VALUES
            (:a2,:assessment,:group_a,clock_timestamp(),clock_timestamp()+interval '1 day',:owner),
            (:a3,:assessment,:group_b,clock_timestamp(),clock_timestamp()+interval '1 day',:owner);
          INSERT INTO assignment_participants(assignment_id,student_id) SELECT :a2,id FROM students
            WHERE class_group_id=:group_a AND archived_at IS NULL
        """), ids)
        rows = (await connection.execute(text("SELECT id,assessment_id,class_group_id FROM assignments ORDER BY id"))).all()
        assert len({row.id for row in rows}) == 3
        assert {row.assessment_id for row in rows} == {ids["assessment"]}
        old = set((await connection.execute(text("SELECT student_id FROM assignment_participants WHERE assignment_id=:a1"), ids)).scalars())
        new = set((await connection.execute(text("SELECT student_id FROM assignment_participants WHERE assignment_id=:a2"), ids)).scalars())
        assert old == {ids["s1"], ids["s2"], ids["s3"]}
        assert new == {ids["s1"], ids["s4"]}
        assert ids["s4"] not in old
