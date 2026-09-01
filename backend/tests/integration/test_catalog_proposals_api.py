"""Real PostgreSQL HTTP acceptance for the catalog proposal boundary."""

import asyncio
import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

database_url = os.environ.get("TEST_DATABASE_URL", "")
if not database_url:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not database_url.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Integration cleanup is allowed only for a database ending in _test")

os.environ["DATABASE_URL"] = database_url

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.application.principal import Principal  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.main import app  # noqa: E402
from tests.integration.auth_helpers import (  # noqa: E402
    admin_principal,
    clear_principal_override,
    override_principal,
    student_principal,
    teacher_principal,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _assert_test_database(session) -> None:
    name = await session.scalar(text("SELECT current_database()"))
    if not name or not name.endswith("_test"):
        raise RuntimeError("Integration cleanup is allowed only for a database ending in _test")


async def _clean() -> None:
    async with async_session_factory() as session, session.begin():
        await _assert_test_database(session)
        await session.execute(text("TRUNCATE skills, subtopics, topics, grades, subjects, users CASCADE"))


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_boundary():
    await _clean()
    clear_principal_override(app)
    yield
    clear_principal_override(app)
    await _clean()


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def dispose_engine():
    yield
    await engine.dispose()


async def _user(label: str) -> UUID:
    async with async_session_factory() as session, session.begin():
        return await session.scalar(
            text("INSERT INTO users(login,normalized_login,display_name,password_hash) "
                 "VALUES (:label,:label,:label,'hash') RETURNING id"),
            {"label": f"{label}-{uuid4()}"},
        )


async def _post(payload, *, origin=None):
    headers = {"Origin": origin} if origin else None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/catalog/proposals", json=payload, headers=headers)


async def _seed(table: str, values: str, params: dict) -> UUID:
    async with async_session_factory() as session, session.begin():
        return await session.scalar(text(f"INSERT INTO {table} {values} RETURNING id"), params)


async def test_teacher_create_repeat_and_second_teacher_preserves_attribution():
    teacher_a, teacher_b = await _user("teacher-a"), await _user("teacher-b")
    override_principal(app, teacher_principal(teacher_a))
    first = await _post({"kind": "subject", "name": "Математика"})
    repeat = await _post({"kind": "subject", "name": "  МАТЕМАТИКА  "})
    override_principal(app, teacher_principal(teacher_b))
    second = await _post({"kind": "subject", "name": "математика"})
    assert (first.status_code, first.json()["outcome"]) == (201, "created_provisional")
    assert (repeat.status_code, repeat.json()["outcome"]) == (200, "existing_provisional")
    assert (second.status_code, second.json()["outcome"]) == (200, "existing_provisional")
    assert first.json()["id"] == repeat.json()["id"] == second.json()["id"]
    async with async_session_factory() as session:
        rows = (await session.execute(text("SELECT normalized_name,status,proposed_by FROM subjects"))).all()
    assert rows == [("математика", "provisional", teacher_a)]


async def test_authorization_matrix_and_trusted_origin():
    teacher, admin = await _user("teacher"), await _user("admin")
    override_principal(app, teacher_principal(teacher))
    foreign = await _post({"kind": "subject", "name": "Foreign"}, origin="https://evil.example")
    assert foreign.status_code == 403 and foreign.json()["error"]["code"] == "foreign_origin"
    override_principal(app, admin_principal(admin))
    allowed = await _post({"kind": "subject", "name": "Admin subject"})
    assert allowed.status_code == 201
    override_principal(app, student_principal(uuid4(), uuid4()))
    student = await _post({"kind": "subject", "name": "Student subject"})
    override_principal(app, Principal(uuid4(), "none", "None", frozenset(), frozenset(), None))
    no_role = await _post({"kind": "subject", "name": "No role subject"})
    clear_principal_override(app)
    anonymous = await _post({"kind": "subject", "name": "Anonymous subject"})
    assert student.status_code == no_role.status_code == 403
    assert anonymous.status_code == 401 and anonymous.json()["error"]["code"] == "authentication_required"
    async with async_session_factory() as session:
        rows = (await session.execute(text("SELECT name,proposed_by FROM subjects"))).all()
    assert rows == [("Admin subject", admin)]


async def test_active_and_deprecated_subject_identity_reuse():
    teacher = await _user("teacher")
    active = await _seed("subjects", "(code,name,normalized_name,status) VALUES ('active','Математика','математика','active')", {})
    deprecated = await _seed("subjects", "(code,name,normalized_name,status) VALUES ('old','Физика','физика','deprecated')", {})
    override_principal(app, teacher_principal(teacher))
    reused = await _post({"kind": "subject", "name": " МАТЕМАТИКА "})
    replacement = await _post({"kind": "subject", "name": "ФИЗИКА"})
    assert reused.status_code == 200 and reused.json()["outcome"] == "existing_active"
    assert reused.json()["id"] == str(active)
    assert replacement.status_code == 201 and replacement.json()["id"] != str(deprecated)
    async with async_session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM subjects WHERE normalized_name='математика'")) == 1
        assert await session.scalar(text("SELECT count(*) FROM subjects WHERE normalized_name='физика'")) == 2


async def test_grade_identity_is_numeric():
    teacher = await _user("teacher")
    grade = await _seed("grades", "(number,name,normalized_name,status) VALUES (8,'8 класс','8 класс','active')", {})
    override_principal(app, teacher_principal(teacher))
    response = await _post({"kind": "grade", "number": 8, "name": "Восьмой класс"})
    assert response.status_code == 200 and response.json()["outcome"] == "existing_active"
    assert response.json()["id"] == str(grade)
    async with async_session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM grades WHERE number=8 AND status IN ('active','provisional')")) == 1


async def test_topic_parents_missing_deprecated_and_valid():
    teacher = await _user("teacher")
    subject = await _seed("subjects", "(code,name,normalized_name,status) VALUES ('s','S','s','active')", {})
    grade = await _seed("grades", "(number,name,normalized_name,status) VALUES (8,'G','g','active')", {})
    deprecated = await _seed("subjects", "(code,name,normalized_name,status) VALUES ('d','D','d','deprecated')", {})
    override_principal(app, teacher_principal(teacher))
    missing = await _post({"kind": "topic", "name": "T", "subject_id": str(uuid4()), "grade_id": str(grade)})
    blocked = await _post({"kind": "topic", "name": "T", "subject_id": str(deprecated), "grade_id": str(grade)})
    created = await _post({"kind": "topic", "name": "T", "subject_id": str(subject), "grade_id": str(grade)})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "catalog_parent_not_found"
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "catalog_parent_deprecated"
    assert created.status_code == 201
    async with async_session_factory() as session:
        row = (await session.execute(text("SELECT subject_id,grade_id FROM topics"))).one()
    assert row == (subject, grade)


async def test_empty_catalog_provisional_chain_and_active_only_visibility():
    teacher = await _user("teacher")
    override_principal(app, teacher_principal(teacher))
    subject = await _post({"kind": "subject", "name": "Математика"})
    grade = await _post({"kind": "grade", "number": 8, "name": "8 класс"})
    topic = await _post({"kind": "topic", "name": "Уравнения", "subject_id": subject.json()["id"], "grade_id": grade.json()["id"]})
    subtopic = await _post({"kind": "subtopic", "name": "Квадратные", "topic_id": topic.json()["id"]})
    skill = await _post({"kind": "skill", "name": "Решать", "subtopic_id": subtopic.json()["id"]})
    responses = [subject, grade, topic, subtopic, skill]
    assert all(r.status_code == 201 and r.json()["status"] == "provisional" for r in responses)
    ids = [UUID(r.json()["id"]) for r in responses]
    async with async_session_factory() as session:
        assert (await session.execute(text("SELECT subject_id,grade_id,proposed_by FROM topics"))).one() == (ids[0], ids[1], teacher)
        assert (await session.execute(text("SELECT topic_id,proposed_by FROM subtopics"))).one() == (ids[2], teacher)
        assert (await session.execute(text("SELECT subtopic_id,proposed_by FROM skills"))).one() == (ids[3], teacher)
        for table in ("subjects", "grades", "topics", "subtopics", "skills"):
            assert await session.scalar(text(f"SELECT count(*) FROM {table} WHERE status='provisional' AND proposed_by=:actor"), {"actor": teacher}) == 1
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        visible = await client.get("/api/content-bank/catalog/subjects")
    assert visible.status_code == 200 and visible.json()["items"] == []


@pytest.mark.parametrize("payload", [
    {"kind": "subject", "name": "Math", "normalized_name": "attack"},
    {"kind": "subject", "name": "Math", "topic_id": "00000000-0000-4000-8000-000000000001"},
    {"kind": "grade", "number": 0, "name": "Zero"},
    {"kind": "grade", "number": 12, "name": "Twelve"},
    {"kind": "subject", "name": "   "},
])
async def test_strict_request_dto_has_no_mutation(payload):
    teacher = await _user("teacher")
    override_principal(app, teacher_principal(teacher))
    response = await _post(payload)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    async with async_session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM subjects")) == 0
        assert await session.scalar(text("SELECT count(*) FROM grades")) == 0


async def test_concurrent_http_topic_proposals_recover_unique_conflict():
    """A stable override avoids racing global dependency override mutation."""
    teacher = await _user("teacher")
    subject = await _seed("subjects", "(code,name,normalized_name,status) VALUES ('s','S','s','active')", {})
    grade = await _seed("grades", "(number,name,normalized_name,status) VALUES (8,'G','g','active')", {})
    override_principal(app, teacher_principal(teacher))
    payload = {"kind": "topic", "name": "Квадратные уравнения", "subject_id": str(subject), "grade_id": str(grade)}

    async def request():
        return await _post(payload)

    first, second = await asyncio.gather(request(), request())
    results = sorted([
        (first.status_code, first.json()["outcome"]),
        (second.status_code, second.json()["outcome"]),
    ])
    assert results == [(200, "existing_provisional"), (201, "created_provisional")]
    assert first.json()["id"] == second.json()["id"]
    async with async_session_factory() as session:
        count = await session.scalar(text("SELECT count(*) FROM topics WHERE subject_id=:s AND grade_id=:g AND normalized_name='квадратные уравнения' AND status IN ('active','provisional')"), {"s": subject, "g": grade})
    assert count == 1
