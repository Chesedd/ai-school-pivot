"""PostgreSQL coverage for account-driven classroom provisioning."""
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.classroom_administration import ClassroomAdministrationService, ClassroomError
from app.infrastructure.assessment_models import Student
from app.infrastructure.auth_models import StudentUserLink
from app.infrastructure.classroom_repository import SQLAlchemyClassroomRepository
from tests.integration.c10a_postgres import rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _fixture(connection):
    ids = {name: uuid4() for name in ("admin", "grade", "group", "other", "active", "inactive", "teacher")}
    for statement in ("""
      INSERT INTO users(id,login,normalized_login,display_name,first_name,last_name,password_hash,is_active) VALUES
       (:admin,'provision-admin','provision-admin','Admin',NULL,NULL,'x',true),
       (:active,'provision-student','provision-student','Canonical Student','Canonical','Student','x',true),
       (:inactive,'provision-inactive','provision-inactive','Inactive Student',NULL,NULL,'x',false),
       (:teacher,'provision-teacher','provision-teacher','Teacher',NULL,NULL,'x',true)
    """, """INSERT INTO user_roles(user_id,role) VALUES (:admin,'admin'),(:active,'student'),(:inactive,'student'),(:teacher,'teacher')
    """, """INSERT INTO grades(id,number,name,normalized_name) VALUES (:grade,8,'Provision Grade','provision grade')
    """, """
      INSERT INTO class_groups(id,name,grade_id,created_by) VALUES (:group,'Provision A',:grade,:admin),(:other,'Provision B',:grade,:admin)
    """):
        await connection.execute(text(statement), ids)
    return ids


async def test_provisions_account_and_link_atomically_and_filters_candidates():
    async with rolled_back_connection() as connection:
        ids = await _fixture(connection)
        factory = async_sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        async with factory() as session, session.begin():
            service = ClassroomAdministrationService(SQLAlchemyClassroomRepository(session))
            candidates = await service.list_student_candidates(ids["group"], "canonical", 10)
            assert [candidate.user_id for candidate in candidates] == [ids["active"]]
            result = await service.create_student(ids["group"], ids["active"], "pupil-1", ids["admin"])
            assert result.display_name == "Canonical Student"
            assert result.user_id == ids["active"] and result.external_ref == "pupil-1"
        row = (await connection.execute(text("""
          SELECT s.id,s.class_group_id,s.display_name,l.user_id
          FROM students s JOIN student_user_links l ON l.student_id=s.id WHERE l.user_id=:active
        """), ids)).one()
        assert row == (result.id, ids["group"], "Canonical Student", ids["active"])


async def test_validation_existing_link_and_external_ref_rollback():
    async with rolled_back_connection() as connection:
        ids = await _fixture(connection)
        factory = async_sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        async with factory() as session, session.begin():
            service = ClassroomAdministrationService(SQLAlchemyClassroomRepository(session))
            for user, code in ((uuid4(), "user_not_found"), (ids["inactive"], "student_user_inactive"), (ids["teacher"], "student_role_required")):
                with pytest.raises(ClassroomError) as error:
                    await service.create_student(ids["group"], user, None, ids["admin"])
                assert error.value.code == code
            await service.create_student(ids["group"], ids["active"], "duplicate", ids["admin"])
            with pytest.raises(ClassroomError) as error:
                await service.create_student(ids["group"], ids["active"], None, ids["admin"])
            assert error.value.code == "student_already_in_class"

            second = uuid4()
            await session.execute(text("INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:id,'provision-second','provision-second','Second','x')"), {"id": second})
            await session.execute(text("INSERT INTO user_roles(user_id,role) VALUES (:id,'student')"), {"id": second})
            before = await session.scalar(select(func.count(Student.id)))
            with pytest.raises(ClassroomError) as error:
                await service.create_student(ids["group"], second, "duplicate", ids["admin"])
            assert error.value.code == "external_ref_conflict"
            assert await session.scalar(select(func.count(Student.id))) == before
            assert await session.get(StudentUserLink, second) is None
