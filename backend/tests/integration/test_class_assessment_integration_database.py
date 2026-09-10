"""C6/C9A Assessment snapshot and generalized-schema database contracts."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from datetime import datetime, timedelta, timezone
from app.application.assessments import AssessmentService, CreateAssignmentCommand
from app.application.content_bank import ActorContext
from app.infrastructure.assessment_repository import SQLAlchemyAssessmentUnitOfWork
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
        fixture_sql = """
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
        """
        for statement in fixture_sql.split(";"):
            if statement.strip():
                await connection.execute(text(statement), ids)
        rows = (await connection.execute(text("SELECT id,assessment_id,class_group_id FROM assignments ORDER BY id"))).all()
        assert len({row.id for row in rows}) == 3
        assert {row.assessment_id for row in rows} == {ids["assessment"]}
        old = set((await connection.execute(text("SELECT student_id FROM assignment_participants WHERE assignment_id=:a1"), ids)).scalars())
        new = set((await connection.execute(text("SELECT student_id FROM assignment_participants WHERE assignment_id=:a2"), ids)).scalars())
        assert old == {ids["s1"], ids["s2"], ids["s3"]}
        assert new == {ids["s1"], ids["s4"]}
        assert ids["s4"] not in old


async def test_real_assignment_application_flow_snapshots_each_current_roster():
    """C6 acceptance: the application operation, not fixture SQL, makes snapshots."""
    from uuid import uuid4

    async with rolled_back_connection() as connection:
        ids = {name: uuid4() for name in
               ("teacher", "grade", "group_a", "group_b", "assessment", "a", "b", "c", "d")}
        fixture_sql = """
          INSERT INTO users(id,login,normalized_login,display_name,password_hash)
            VALUES (:teacher,'c10d3-app-owner','c10d3-app-owner','Teacher A','hash');
          INSERT INTO user_roles(user_id,role) VALUES (:teacher,'teacher');
          INSERT INTO grades(id,number,name,normalized_name)
            VALUES (:grade,7,'Application Grade 7','application grade 7');
          INSERT INTO class_groups(id,name,grade_id,created_by) VALUES
            (:group_a,'Application 7A',:grade,:teacher),
            (:group_b,'Application 7B',:grade,:teacher);
          INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by)
            VALUES (:group_a,:teacher,:teacher);
          INSERT INTO students(id,class_group_id,display_name) VALUES
            (:a,:group_a,'A'),(:b,:group_a,'B'),(:c,:group_a,'C');
          INSERT INTO assessments(id,title,status,created_by,published_at,published_by)
            VALUES (:assessment,'Published X','published',:teacher,clock_timestamp(),:teacher)
        """
        for statement in fixture_sql.split(";"):
            if statement.strip():
                await connection.execute(text(statement), ids)
        factory = async_sessionmaker(bind=connection, expire_on_commit=False,
                                     join_transaction_mode="create_savepoint")
        service = AssessmentService(SQLAlchemyAssessmentUnitOfWork(factory))
        actor = ActorContext(ids["teacher"])
        now = datetime.now(timezone.utc)

        first = await service.create_assignment(CreateAssignmentCommand(
            ids["assessment"], ids["group_a"], now, now + timedelta(days=1), 1), actor)
        assert first.assessment_id == ids["assessment"]
        assert first.class_group_id == ids["group_a"]
        assert first.participant_count == 3
        assert set(first.participant_ids) == {ids["a"], ids["b"], ids["c"]}

        fixture_sql = """
          UPDATE students SET class_group_id=:group_b WHERE id=:b;
          UPDATE students SET archived_at=clock_timestamp() WHERE id=:c;
          INSERT INTO students(id,class_group_id,display_name) VALUES (:d,:group_a,'D')
        """
        for statement in fixture_sql.split(";"):
            if statement.strip():
                await connection.execute(text(statement), ids)
        second = await service.create_assignment(CreateAssignmentCommand(
            ids["assessment"], ids["group_a"], now, now + timedelta(days=2), 1), actor)

        assert second.id != first.id
        old = set((await connection.execute(text(
            "SELECT student_id FROM assignment_participants WHERE assignment_id=:id"),
            {"id": first.id})).scalars())
        new = set((await connection.execute(text(
            "SELECT student_id FROM assignment_participants WHERE assignment_id=:id"),
            {"id": second.id})).scalars())
        assert old == {ids["a"], ids["b"], ids["c"]}
        assert new == {ids["a"], ids["d"]}
