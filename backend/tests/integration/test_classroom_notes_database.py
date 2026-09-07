"""C5 private-note PostgreSQL acceptance scenarios."""

from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.infrastructure.classroom_repository import SQLAlchemyClassroomNotesRepository
from tests.integration.c10a_postgres import rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_private_notes_idor_cas_reassignment_and_move_snapshot():
    async with rolled_back_connection() as connection:
        v = {key: uuid4() for key in ("admin","a","b","grade","source","destination","student")}
        await connection.execute(text("""
          INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES
            (:admin,'notes-admin','notes-admin','Admin','x'),(:a,'notes-a','notes-a','A','x'),(:b,'notes-b','notes-b','B','x');
          INSERT INTO grades(id,number,name,normalized_name) VALUES (:grade,7,'Notes grade','notes grade');
          INSERT INTO class_groups(id,name,grade_id,created_by) VALUES (:source,'Notes A',:grade,:admin),(:destination,'Notes B',:grade,:admin);
          INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:source,:a,:admin),(:source,:b,:admin);
          INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:source,'Student');
        """), v)
        session_factory = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with session_factory() as session:
            repo = SQLAlchemyClassroomNotesRepository(session)
            class_note = await repo.create_note("class", v["source"], None, v["a"], "private")
            student_note = await repo.create_note("student", v["source"], v["student"], v["a"], "historical")
            assert (await repo.get_note("class", v["source"], None, class_note.id, v["a"], False)).body == "private"
            assert await repo.get_note("class", v["source"], None, class_note.id, v["b"], False) is None
            assert (await repo.get_note("class", v["source"], None, class_note.id, v["admin"], True)).body == "private"
            assert await repo.update_note("class", class_note.id, v["a"], "changed", class_note.updated_at) is not None
            assert await repo.update_note("class", class_note.id, v["a"], "stale", class_note.updated_at) is None
            await session.execute(text("DELETE FROM class_group_teachers WHERE class_group_id=:source AND teacher_user_id=:a"), v)
            assert await repo.class_state(v["source"], v["a"], False) is None
            await session.execute(text("INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:source,:a,:admin)"), v)
            await session.execute(text("UPDATE students SET class_group_id=:destination WHERE id=:student"), v)
            stored = await session.execute(text("SELECT class_group_id FROM student_notes WHERE id=:id"), {"id": student_note.id})
            assert stored.scalar_one() == v["source"]
