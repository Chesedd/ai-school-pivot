"""Real PostgreSQL HTTP regression for task-backed provisional subject navigation."""

import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

url = os.environ.get("TEST_DATABASE_URL", "")
if not url:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not url.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Integration cleanup is allowed only for a database ending in _test")
os.environ["DATABASE_URL"] = url

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.main import app  # noqa: E402
from tests.integration.auth_helpers import (  # noqa: E402
    admin_principal, clear_principal_override, override_principal, teacher_principal,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def clean():
    async with async_session_factory() as session, session.begin():
        database = await session.scalar(text("SELECT current_database()"))
        if not database or not database.endswith("_test"):
            raise RuntimeError("Refusing to clean a non-test database")
        await session.execute(text("TRUNCATE subjects, grades, topics, subtopics, tasks, users CASCADE"))


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def boundary():
    await clean()
    clear_principal_override(app)
    yield
    clear_principal_override(app)
    await clean()


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def dispose():
    yield
    await engine.dispose()


async def seed():
    teacher_a, teacher_b, admin = uuid4(), uuid4(), uuid4()
    active, provisional = uuid4(), uuid4()
    grade, topic, task, version = uuid4(), uuid4(), uuid4(), uuid4()
    async with async_session_factory() as session, session.begin():
        for actor, label in ((teacher_a, "teacher-a"), (teacher_b, "teacher-b"), (admin, "admin")):
            await session.execute(text("INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:id,:x,:x,:x,'hash')"), {"id": actor, "x": f"{label}-{actor}"})
        await session.execute(text("INSERT INTO subjects(id,code,name,normalized_name,status,proposed_by) VALUES (:active,'physics','Физика','физика','active',NULL),(:provisional,'math','Математика','математика','provisional',:teacher)"), {"active": active, "provisional": provisional, "teacher": teacher_a})
        await session.execute(text("INSERT INTO grades(id,number,name,normalized_name) VALUES (:id,7,'7 класс','7 класс')"), {"id": grade})
        await session.execute(text("INSERT INTO topics(id,subject_id,grade_id,code,name,normalized_name,status) VALUES (:id,:subject,:grade,'equations','Уравнения','уравнения','provisional')"), {"id": topic, "subject": provisional, "grade": grade})
        await session.execute(text("INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:id,:subject,:grade,:topic,:actor)"), {"id": task, "subject": provisional, "grade": grade, "topic": topic, "actor": teacher_a})
        await session.execute(text("INSERT INTO task_versions(id,task_id,version_no,title,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:id,:task,1,'Линейное уравнение','7(X - 3) = 21','calculation','number',25,'draft',:actor)"), {"id": version, "task": task, "actor": teacher_a})
    return teacher_a, teacher_b, admin, active, provisional, task


async def navigation(client):
    response = await client.get("/api/content-bank/navigation/subjects")
    assert response.status_code == 200
    return response.json()["items"]


async def test_provisional_subject_navigation_workflow_access_and_confirmation():
    teacher_a, teacher_b, admin, active, provisional, task = await seed()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        override_principal(app, teacher_principal(teacher_a))
        roots = await navigation(client)
        assert {item["id"] for item in roots} == {str(active), str(provisional)}
        assert next(item for item in roots if item["id"] == str(active))["status"] == "active"
        assert next(item for item in roots if item["id"] == str(provisional))["status"] == "provisional"

        catalog = await client.get("/api/content-bank/catalog/subjects")
        assert catalog.status_code == 200
        assert {item["id"] for item in catalog.json()["items"]} == {str(active)}

        contents = await client.get(f"/api/content-bank/subjects/{provisional}/contents")
        assert contents.status_code == 200
        assert [item["task_id"] for item in contents.json()["tasks"]["items"]] == [str(task)]

        async with async_session_factory() as session, session.begin():
            await session.execute(text("UPDATE task_versions SET status='review' WHERE task_id=:task"), {"task": task})
        assert str(provisional) in {item["id"] for item in await navigation(client)}

        override_principal(app, teacher_principal(teacher_b))
        other_roots = await navigation(client)
        assert str(provisional) not in {item["id"] for item in other_roots}
        assert "Математика" not in {item["name"] for item in other_roots}

        override_principal(app, admin_principal(admin))
        assert str(provisional) in {item["id"] for item in await navigation(client)}
        confirmed = await client.post(f"/api/catalog/proposals/subject/{provisional}/confirm", json={})
        assert confirmed.status_code == 200
        confirmed_roots = await navigation(client)
        matches = [item for item in confirmed_roots if item["id"] == str(provisional)]
        assert matches == [{"id": str(provisional), "name": "Математика", "status": "active"}]
