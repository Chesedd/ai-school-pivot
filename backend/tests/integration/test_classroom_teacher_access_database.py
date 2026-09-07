"""Real-PostgreSQL proofs for C2 membership integrity and C4 access scoping."""
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.classroom_repository import SQLAlchemyClassroomAccessRepository

URL = os.environ.get("TEST_DATABASE_URL", "")
if not URL:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Classroom DB tests require a database ending in _test")
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def database():
    engine = create_async_engine(URL)
    ids = {name: uuid4() for name in (
        "admin", "teacher_a", "teacher_b", "grade7", "grade8", "class7a",
        "class7b", "class8a", "legacy", "archived", "student7a", "student7b")}
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE classroom_audit_log, class_group_teachers, assignment_participants, assignments, assessments, student_user_links, students, class_groups, auth_sessions, user_roles, users, grades CASCADE"))
        for role in ("admin", "teacher_a", "teacher_b"):
            await connection.execute(text("INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:id,:login,:login,:name,'hash')"), {"id": ids[role], "login": role, "name": role})
        await connection.execute(text("INSERT INTO user_roles(user_id,role) VALUES (:admin,'admin'),(:teacher_a,'teacher'),(:teacher_b,'teacher')"), ids)
        await connection.execute(text("INSERT INTO grades(id,number,name,normalized_name) VALUES (:grade7,7,'7 класс','7 класс'),(:grade8,8,'8 класс','8 класс')"), ids)
        await connection.execute(text("""INSERT INTO class_groups(id,name,grade_id,created_by,archived_at) VALUES
            (:class7a,'7А',:grade7,:admin,NULL),(:class7b,'7Б',:grade7,:admin,NULL),
            (:class8a,'8А',:grade8,:admin,NULL),(:legacy,'Legacy',NULL,:admin,NULL),
            (:archived,'Архив',:grade8,:admin,clock_timestamp())"""), ids)
        await connection.execute(text("""INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES
            (:class7a,:teacher_a,:admin),(:class8a,:teacher_a,:admin),(:legacy,:teacher_a,:admin),
            (:archived,:teacher_a,:admin),(:class7b,:teacher_b,:admin)"""), ids)
        await connection.execute(text("INSERT INTO students(id,class_group_id,display_name) VALUES (:student7a,:class7a,'Анна'),(:student7b,:class7b,'Борис')"), ids)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), engine, ids
    finally:
        await engine.dispose()


async def test_teacher_and_admin_listing_is_membership_scoped_in_sql(database):
    factory, _, ids = database
    async with factory() as session:
        access = SQLAlchemyClassroomAccessRepository(session)
        teacher_a = await access.list_accessible_classes(ids["teacher_a"], False, "active", 0, 20)
        teacher_b = await access.list_accessible_classes(ids["teacher_b"], False, "active", 0, 20)
        admin = await access.list_accessible_classes(ids["admin"], True, "all", 0, 20)
        assert {row.name for row in teacher_a["items"]} == {"7А", "8А", "Legacy"}
        assert {row.name for row in teacher_b["items"]} == {"7Б"}
        assert {row.name for row in admin["items"]} == {"7А", "7Б", "8А", "Legacy", "Архив"}


async def test_known_foreign_uuid_cannot_open_class_or_roster(database):
    factory, _, ids = database
    async with factory() as session:
        access = SQLAlchemyClassroomAccessRepository(session)
        assert await access.get_accessible_class(ids["class7b"], ids["teacher_a"], False) is None
        assert await access.list_accessible_students(ids["class7b"], ids["teacher_a"], False, "active") is None
        assert (await access.get_accessible_class(ids["class7b"], ids["admin"], True)).name == "7Б"


async def test_assessment_choices_exclude_foreign_archived_and_grade_null(database):
    factory, _, ids = database
    async with factory() as session:
        access = SQLAlchemyClassroomAccessRepository(session)
        teacher = await access.list_assignment_eligible_classes(ids["teacher_a"], False, 0, 20)
        admin = await access.list_assignment_eligible_classes(ids["admin"], True, 0, 20)
        assert {row[1] for row in teacher["items"]} == {"7А", "8А"}
        assert {row[1] for row in admin["items"]} == {"7А", "7Б", "8А"}
        assert not await access.can_access_class(ids["class7b"], ids["teacher_a"], False,
                                                 require_active=True, require_configured=True, lock=True)


async def test_unassignment_and_reassignment_take_effect_without_row_copying(database):
    factory, _, ids = database
    async with factory() as session:
        access = SQLAlchemyClassroomAccessRepository(session)
        assert await access.can_access_class(ids["class7a"], ids["teacher_a"], False)
        await session.execute(text("DELETE FROM class_group_teachers WHERE class_group_id=:class7a AND teacher_user_id=:teacher_a"), ids)
        await session.commit()
        assert not await access.can_access_class(ids["class7a"], ids["teacher_a"], False)
        await session.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:class7a,:teacher_a,:admin)"), ids)
        await session.commit()
        assert await access.can_access_class(ids["class7a"], ids["teacher_a"], False)


async def test_c2_membership_composite_key_and_foreign_keys(database):
    _, engine, ids = database
    async with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:class7a,:teacher_a,:admin)"), ids)
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:class7a,:missing,:admin)"), {**ids, "missing": uuid4()})
