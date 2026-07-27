"""PostgreSQL-only API integration slice.

Run against a migrated database whose name ends in ``_test``.  The guard is
deliberately evaluated before any destructive cleanup.
"""
import os
from uuid import uuid4

import pytest
import pytest_asyncio

database_url = os.environ.get("TEST_DATABASE_URL", "")
if not database_url:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not database_url.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Integration cleanup is allowed only for a database ending in _test")

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

os.environ["DATABASE_URL"] = database_url
os.environ.setdefault("CONTENT_BANK_DEV_ACTOR_ID", "00000000-0000-4000-8000-000000000001")

from app.db.session import async_session_factory, engine  # noqa: E402
from app.main import app  # noqa: E402

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def dispose_application_engine():
    """Dispose the shared application pool before its session loop closes."""
    try:
        yield
    finally:
        await engine.dispose()


async def _assert_test_database(session) -> None:
    """Refuse destructive operations unless PostgreSQL confirms a test DB."""
    current_database = await session.scalar(text("SELECT current_database()"))
    if not current_database or not current_database.endswith("_test"):
        raise RuntimeError(
            "Integration cleanup is allowed only for a database ending in _test"
        )


async def _cleanup_catalog() -> None:
    async with async_session_factory() as session:
        async with session.begin():
            await _assert_test_database(session)
            await session.execute(
                text(
                    "TRUNCATE task_skill_links, task_versions, tasks, skills, "
                    "subtopics, topics, grades, subjects CASCADE"
                )
            )


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def catalog():
    ids = {name: uuid4() for name in ("subject", "grade", "topic", "subtopic", "skill", "other_topic", "other_subtopic", "other_skill")}
    async with async_session_factory() as session:
        async with session.begin():
            await _assert_test_database(session)
            await session.execute(text("TRUNCATE task_skill_links, task_versions, tasks, skills, subtopics, topics, grades, subjects CASCADE"))
            await session.execute(text("INSERT INTO subjects(id,code,name) VALUES (:id,'s','Subject')"), {"id": ids["subject"]})
            await session.execute(text("INSERT INTO grades(id,number,name) VALUES (:id,7,'Grade')"), {"id": ids["grade"]})
            await session.execute(text("INSERT INTO topics(id,subject_id,grade_id,code,name) VALUES (:id,:s,:g,'t','Topic'),(:other,:s,:g,'o','Other')"), {"id": ids["topic"], "other": ids["other_topic"], "s": ids["subject"], "g": ids["grade"]})
            await session.execute(text("INSERT INTO subtopics(id,topic_id,code,name) VALUES (:id,:t,'st','Subtopic'),(:other,:ot,'ost','Other')"), {"id": ids["subtopic"], "t": ids["topic"], "other": ids["other_subtopic"], "ot": ids["other_topic"]})
            await session.execute(text("INSERT INTO skills(id,subtopic_id,code,name) VALUES (:id,:st,'sk','Skill'),(:other,:ost,'osk','Other')"), {"id": ids["skill"], "st": ids["subtopic"], "other": ids["other_skill"], "ost": ids["other_subtopic"]})
    try:
        yield ids
    finally:
        await _cleanup_catalog()


def payload(ids):
    return {"subject_id": str(ids["subject"]), "grade_id": str(ids["grade"]), "topic_id": str(ids["topic"]), "subtopic_id": str(ids["subtopic"]), "initial_version": {"title": None, "statement": "Test", "task_type": "calculation", "answer_format": "number", "difficulty": "basic", "source": None, "skills": [{"skill_id": str(ids["skill"]), "weight": "1.0000", "is_primary": True}]}}


async def test_create_is_atomic_and_server_owned(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/content-bank/tasks", json=payload(catalog))
    assert response.status_code == 201
    body = response.json(); assert body["initial_version"]["version_no"] == 1 and body["initial_version"]["status"] == "draft"
    async with async_session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM tasks")) == 1
        assert await session.scalar(text("SELECT count(*) FROM task_versions WHERE version_no=1 AND status='draft'")) == 1
        assert await session.scalar(text("SELECT count(*) FROM task_skill_links")) == 1


async def test_client_cannot_set_actor(catalog):
    data = payload(catalog); data["created_by"] = str(uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client: response = await client.post("/api/content-bank/tasks", json=data)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("mismatch", ["unknown_skill", "topic", "subtopic", "skill"])
async def test_catalog_mismatch_leaves_no_task(catalog, mismatch):
    data = payload(catalog)
    if mismatch == "unknown_skill": data["initial_version"]["skills"][0]["skill_id"] = str(uuid4())
    elif mismatch == "topic": data["topic_id"] = str(catalog["other_topic"])
    elif mismatch == "subtopic": data["subtopic_id"] = str(catalog["other_subtopic"])
    else: data["initial_version"]["skills"][0]["skill_id"] = str(catalog["other_skill"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client: response = await client.post("/api/content-bank/tasks", json=data)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    async with async_session_factory() as session: assert await session.scalar(text("SELECT count(*) FROM tasks")) == 0


async def test_catalog_returns_seed_data(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client: response = await client.get("/api/content-bank/catalog/subjects")
    assert response.status_code == 200
    subjects = {item["id"]: item for item in response.json()["items"]}
    assert subjects[str(catalog["subject"])]["name"] == "Subject"


async def _insert_archived_task(catalog) -> str:
    task_id, version_id = uuid4(), uuid4()
    async with async_session_factory() as session:
        async with session.begin():
            await _assert_test_database(session)
            await session.execute(
                text(
                    "INSERT INTO tasks(id,subject_id,grade_id,topic_id,subtopic_id,created_by,archived_at) "
                    "VALUES (:id,:subject,:grade,:topic,:subtopic,:actor,CURRENT_TIMESTAMP)"
                ),
                {"id": task_id, "subject": catalog["subject"], "grade": catalog["grade"], "topic": catalog["topic"], "subtopic": catalog["subtopic"], "actor": uuid4()},
            )
            await session.execute(
                text(
                    "INSERT INTO task_versions(id,task_id,version_no,title,statement,task_type,answer_format,difficulty,status,created_by) "
                    "VALUES (:id,:task,1,'Archived','Archived statement','calculation','number','basic','archived',:actor)"
                ),
                {"id": version_id, "task": task_id, "actor": uuid4()},
            )
            await session.execute(
                text(
                    "INSERT INTO task_skill_links(task_version_id,skill_id,weight,is_primary) "
                    "VALUES (:version,:primary,0.6000,true),(:version,:secondary,0.4000,false)"
                ),
                {"version": version_id, "primary": catalog["skill"], "secondary": catalog["other_skill"]},
            )
    return str(task_id)


async def test_archived_task_is_hidden_without_status(catalog):
    archived_id = await _insert_archived_task(catalog)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/content-bank/tasks")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "offset": 0, "limit": 20}
    assert archived_id not in {item["task_id"] for item in response.json()["items"]}


async def test_archived_status_returns_one_unduplicated_card(catalog):
    archived_id = await _insert_archived_task(catalog)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/content-bank/tasks", params={"status": "archived", "limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert [item["task_id"] for item in body["items"]] == [archived_id]
    assert len({item["task_id"] for item in body["items"]}) == len(body["items"])


async def test_draft_status_does_not_return_archived_card(catalog):
    archived_id = await _insert_archived_task(catalog)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/content-bank/tasks", params={"status": "draft"})
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert archived_id not in {item["task_id"] for item in response.json()["items"]}

async def test_post_get_roundtrip_and_location(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/content-bank/tasks", json=payload(catalog))
        response = await client.get(created.headers["Location"])
    assert created.status_code == 201 and response.status_code == 200
    body = response.json()
    assert created.headers["Location"] == f'/api/content-bank/tasks/{created.json()["id"]}'
    assert body["id"] == created.json()["id"]
    assert body["subject"] == {"id": str(catalog["subject"]), "name": "Subject"}
    assert body["grade"]["name"] == "Grade" and body["topic"]["name"] == "Topic"
    assert body["subtopic"]["name"] == "Subtopic"
    assert body["latest_version"]["created_by"] == "00000000-0000-4000-8000-000000000001"
    assert body["latest_version"]["skills"][0]["is_primary"] is True
    assert body["approved_version"] is None
    assert [item["version_no"] for item in body["versions"]] == [1]


async def test_card_not_found_and_invalid_uuid(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get(f"/api/content-bank/tasks/{uuid4()}")
        invalid = await client.get("/api/content-bank/tasks/not-a-uuid")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "validation_error"


async def test_archived_card_remains_available_by_id(catalog):
    task_id = await _insert_archived_task(catalog)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/content-bank/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["archived_at"] is not None


async def test_latest_and_historical_approved_versions(catalog):
    task_id, v1, v2, actor = uuid4(), uuid4(), uuid4(), uuid4()
    async with async_session_factory() as session:
        async with session.begin():
            await _assert_test_database(session)
            await session.execute(text("INSERT INTO tasks(id,subject_id,grade_id,topic_id,subtopic_id,created_by) VALUES (:id,:s,:g,:t,:st,:a)"), {"id":task_id,"s":catalog["subject"],"g":catalog["grade"],"t":catalog["topic"],"st":catalog["subtopic"],"a":actor})
            await session.execute(text("INSERT INTO task_versions(id,task_id,version_no,title,statement,task_type,answer_format,difficulty,status,created_by,approved_by,approved_at) VALUES (:v1,:task,1,'Old','Approved','calculation','number','basic','archived',:a,:a,CURRENT_TIMESTAMP - interval '1 day'),(:v2,:task,2,'New','Draft','calculation','number','advanced','draft',:a,NULL,NULL)"), {"v1":v1,"v2":v2,"task":task_id,"a":actor})
            await session.execute(text("INSERT INTO task_skill_links(task_version_id,skill_id,weight,is_primary) VALUES (:v2,:skill,1.0000,true)"), {"v2":v2,"skill":catalog["skill"]})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/content-bank/tasks/{task_id}")
    body=response.json()
    assert response.status_code == 200
    assert body["latest_version"]["id"] == str(v2) and body["latest_version"]["version_no"] == 2
    assert body["approved_version"]["id"] == str(v1) and body["approved_version"]["status"] == "archived"
    assert [x["version_no"] for x in body["versions"]] == [2, 1]
    assert len(body["latest_version"]["skills"]) == len({x["skill_id"] for x in body["latest_version"]["skills"]})


async def test_existing_list_and_post_still_work_with_card_route(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/content-bank/tasks", json=payload(catalog))
        listed = await client.get("/api/content-bank/tasks")
    assert created.status_code == 201 and listed.status_code == 200
    assert listed.json()["items"][0]["task_id"] == created.json()["id"]
