"""C2 administration invariants executed by PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration.c10a_postgres import rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _seed(connection):
    ids = {key: uuid4() for key in ("admin", "teacher", "grade", "group", "other", "student")}
    await connection.execute(text("""
      INSERT INTO users(id,login,normalized_login,display_name,password_hash)
      VALUES (:admin,'c10a-admin','c10a-admin','Admin','x'),
             (:teacher,'c10a-teacher','c10a-teacher','Teacher','x');
      INSERT INTO grades(id,number,name,normalized_name) VALUES (:grade,7,'C10A grade','c10a grade');
      INSERT INTO class_groups(id,name,grade_id,created_by) VALUES
        (:group,'C10A 7A',:grade,:admin),(:other,'C10A 7B',:grade,:admin);
      INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by)
        VALUES (:group,:teacher,:admin);
      INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,'Student');
    """), ids)
    return ids


async def test_grade_and_teacher_membership_foreign_keys_and_uniqueness():
    async with rolled_back_connection() as connection:
        ids = await _seed(connection)
        for statement, values in (
            ("INSERT INTO class_groups(id,name,grade_id,created_by) VALUES (:id,'Bad grade',:bad,:admin)", {**ids, "id": uuid4(), "bad": uuid4()}),
            ("INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group,:teacher,:admin)", ids),
            ("INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group,:bad,:admin)", {**ids, "bad": uuid4()}),
            ("INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:other,:teacher,:bad)", {**ids, "bad": uuid4()}),
        ):
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(text(statement), values)


async def test_archive_and_move_preserve_student_and_historical_participation():
    async with rolled_back_connection() as connection:
        ids = await _seed(connection)
        ids.update({"assessment": uuid4(), "assignment": uuid4(), "participant": uuid4()})
        await connection.execute(text("""
          INSERT INTO assessments(id,title,status,created_by,published_at,published_by)
            VALUES (:assessment,'Published','published',:teacher,clock_timestamp(),:teacher);
          INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by)
            VALUES (:assignment,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 day',:teacher);
          INSERT INTO assignment_participants(id,assignment_id,student_id)
            VALUES (:participant,:assignment,:student);
          UPDATE students SET class_group_id=:other WHERE id=:student;
          UPDATE class_groups SET archived_at=clock_timestamp() WHERE id=:group;
        """), ids)
        row = (await connection.execute(text("""
          SELECT s.class_group_id, p.student_id, p.assignment_id,
                 EXISTS(SELECT 1 FROM class_group_teachers t WHERE t.class_group_id=:group)
          FROM students s JOIN assignment_participants p ON p.student_id=s.id WHERE s.id=:student
        """), ids)).one()
        assert row == (ids["other"], ids["student"], ids["assignment"], True)
