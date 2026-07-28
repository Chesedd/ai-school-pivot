"""PostgreSQL-only API integration slice.

Run against a migrated database whose name ends in ``_test``.  The guard is
deliberately evaluated before any destructive cleanup.
"""
import os
from datetime import datetime
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
                    "TRUNCATE audit_log, task_skill_links, task_versions, tasks, skills, "
                    "subtopics, topics, grades, subjects CASCADE"
                )
            )


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def catalog():
    ids = {name: uuid4() for name in ("subject", "grade", "topic", "subtopic", "skill", "other_topic", "other_subtopic", "other_skill")}
    async with async_session_factory() as session:
        async with session.begin():
            await _assert_test_database(session)
            await session.execute(text("TRUNCATE audit_log, task_skill_links, task_versions, tasks, skills, subtopics, topics, grades, subjects CASCADE"))
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


def methodology(ids):
    return {"expected_solution":{"solution_text":"Solution","final_answer":"3","solution_steps":["First","Second"]},"rubric":{"grading_mode":"points","notes":None,"items":[{"criterion":"First criterion","max_points":"1.2500","required":True,"common_failure":None},{"criterion":"Second criterion","max_points":"0.7500","required":False,"common_failure":"Failure"}]},"accepted_answers":[{"answer_value":"3","tolerance":"0.01","unit":None,"normalization_rule":None}],"typical_errors":[{"skill_id":str(ids["skill"]),"code":"sign","title":"Sign","description":"Wrong sign","severity":"medium","remediation_hint":None,"detection_hint":"See line"}],"hints":[{"level":1,"hint_text":"One"},{"level":2,"hint_text":"Two"}]}


async def test_methodology_put_get_replace_and_catalog_reuse(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/content-bank/tasks", json=payload(catalog))
        version_id = created.json()["initial_version"]["id"]
        first = await client.put(f"/api/content-bank/task-versions/{version_id}/methodology", json=methodology(catalog))
        card = await client.get(created.headers["Location"])
        replacement = {"expected_solution":None,"rubric":None,"accepted_answers":[],"typical_errors":methodology(catalog)["typical_errors"],"hints":[{"level":1,"hint_text":"Replacement"}]}
        second = await client.put(f"/api/content-bank/task-versions/{version_id}/methodology", json=replacement)
    assert first.status_code == 200
    assert first.json()["rubric"]["max_score"] == "2.0000"
    assert [x["order_index"] for x in first.json()["rubric"]["items"]] == [0, 1]
    assert [x["level"] for x in card.json()["latest_version"]["methodology"]["hints"]] == [1, 2]
    assert second.status_code == 200 and second.json()["expected_solution"] is None and second.json()["accepted_answers"] == []
    assert second.json()["typical_errors"][0]["id"] == first.json()["typical_errors"][0]["id"]
    async with async_session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM typical_errors")) == 1
        assert await session.scalar(text("SELECT count(*) FROM rubric_items")) == 0


async def test_methodology_conflict_rolls_back_and_envelopes(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/content-bank/tasks", json=payload(catalog)); version_id=created.json()["initial_version"]["id"]
        assert (await client.put(f"/api/content-bank/task-versions/{version_id}/methodology", json=methodology(catalog))).status_code == 200
        conflicting = methodology(catalog); conflicting["expected_solution"]["solution_text"]="Must rollback"; conflicting["typical_errors"][0]["title"]="Different"
        response = await client.put(f"/api/content-bank/task-versions/{version_id}/methodology", json=conflicting)
        missing = await client.put(f"/api/content-bank/task-versions/{uuid4()}/methodology", json=methodology(catalog))
        invalid = await client.put("/api/content-bank/task-versions/nope/methodology", json={})
        card = await client.get(created.headers["Location"])
    assert response.status_code == 409 and response.json()["error"]["code"] == "typical_error_definition_conflict"
    assert card.json()["latest_version"]["methodology"]["expected_solution"]["solution_text"] == "Solution"
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "validation_error"


async def test_methodology_rejects_foreign_skill_and_non_number_tolerance(catalog):
    data=payload(catalog); data["initial_version"]["answer_format"]="short_text"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created=await client.post("/api/content-bank/tasks",json=data); version_id=created.json()["initial_version"]["id"]
        method=methodology(catalog); method["typical_errors"][0]["skill_id"]=str(catalog["other_skill"])
        response=await client.put(f"/api/content-bank/task-versions/{version_id}/methodology",json=method)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    async with async_session_factory() as session: assert await session.scalar(text("SELECT count(*) FROM expected_solutions")) == 0

async def test_status_cycle_warnings_reason_and_strict_rollback(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/content-bank/tasks", json=payload(catalog)); task_id = created.json()["id"]
        review = await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/submit-review", json={})
        failed = await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/approve", json={})
        missing_reason = await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/return-to-draft", json={})
        blank_reason = await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/return-to-draft", json={"reason":"   "})
        returned = await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/return-to-draft", json={"reason":"Add rubric"})
    assert review.status_code == 200 and review.json()["status"] == "review"
    assert review.json()["validation"]["valid_for_approval"] is False
    assert {x["code"] for x in review.json()["validation"]["issues"]} == {"missing_expected_solution", "missing_rubric"}
    assert failed.status_code == 422 and failed.json()["error"]["code"] == "approval_requirements_not_met"
    assert {x["code"] for x in failed.json()["error"]["details"]["issues"]} == {"missing_expected_solution", "missing_rubric"}
    assert missing_reason.status_code == blank_reason.status_code == 422
    assert returned.status_code == 200 and returned.json()["status"] == "draft"


async def test_approve_clone_archive_and_server_metadata(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/content-bank/tasks", json=payload(catalog)); task_id = created.json()["id"]; v1 = created.json()["initial_version"]["id"]
        assert (await client.put(f"/api/content-bank/task-versions/{v1}/methodology", json=methodology(catalog))).status_code == 200
        assert (await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/submit-review", json={})).status_code == 200
        approved = await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/approve", json={})
        created_v2 = await client.post(f"/api/content-bank/tasks/{task_id}/versions", json={"source_version_no":1})
        duplicate = await client.post(f"/api/content-bank/tasks/{task_id}/versions", json={"source_version_no":1})
        assert (await client.post(f"/api/content-bank/tasks/{task_id}/versions/2/submit-review", json={})).status_code == 200
        approved_v2 = await client.post(f"/api/content-bank/tasks/{task_id}/versions/2/approve", json={})
        card = await client.get(f"/api/content-bank/tasks/{task_id}")
    assert approved.status_code == 200 and approved.json()["approved_by"] == "00000000-0000-4000-8000-000000000001"
    assert approved.json()["approved_at"] is not None
    assert created_v2.status_code == 201 and created_v2.json()["status"] == "draft" and created_v2.json()["approved_at"] is None
    assert created_v2.headers["Location"] == f"/api/content-bank/tasks/{task_id}"
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "invalid_source_version"
    assert approved_v2.status_code == 200
    latest = card.json()["latest_version"]
    assert latest["id"] != v1 and latest["version_no"] == 2
    assert latest["status"] == "approved" and card.json()["approved_version"]["id"] == latest["id"]
    assert card.json()["versions"][1]["status"] == "archived" and card.json()["versions"][1]["approved_at"] is not None
    assert latest["methodology"]["expected_solution"]["id"] != methodology(catalog).get("id")
    assert len(latest["methodology"]["typical_errors"]) == len(methodology(catalog)["typical_errors"])
    async with async_session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM task_skill_links")) == 2
        assert await session.scalar(text("SELECT count(*) FROM expected_solutions")) == 2
        assert await session.scalar(text("SELECT count(*) FROM rubrics")) == 2
        assert await session.scalar(text("SELECT count(*) FROM rubric_items")) == 4
        assert await session.scalar(text("SELECT count(*) FROM accepted_answers")) == 2
        assert await session.scalar(text("SELECT count(*) FROM task_error_links")) == 2
        assert await session.scalar(text("SELECT count(*) FROM typical_errors")) == 1
        assert await session.scalar(text("SELECT count(*) FROM hints")) == 4


@pytest.mark.parametrize("initial_status", ["draft", "review", "approved"])
async def test_archive_is_idempotent_visible_and_blocks_actions(catalog, initial_status):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/content-bank/tasks", json=payload(catalog)); task_id=created.json()["id"]
        if initial_status in {"review", "approved"}:
            if initial_status == "approved":
                version_id = created.json()["initial_version"]["id"]
                await client.put(f"/api/content-bank/task-versions/{version_id}/methodology", json=methodology(catalog))
            await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/submit-review", json={})
        if initial_status == "approved":
            await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/approve", json={})
        first = await client.post(f"/api/content-bank/tasks/{task_id}/archive")
        second = await client.post(f"/api/content-bank/tasks/{task_id}/archive")
        card = await client.get(f"/api/content-bank/tasks/{task_id}")
        listed = await client.get("/api/content-bank/tasks", params={"status":"archived"})
        blocked = await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/submit-review", json={})
    assert first.status_code == second.status_code == 200 and first.json()["archived_at"] == second.json()["archived_at"]
    assert card.status_code == 200 and card.json()["latest_version"]["status"] == "archived"
    assert listed.json()["total"] == 1 and len(listed.json()["items"]) == 1
    assert blocked.status_code == 409


async def test_status_command_not_found_and_invalid_uuid_envelopes(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post(f"/api/content-bank/tasks/{uuid4()}/versions/1/submit-review", json={})
        invalid = await client.post("/api/content-bank/tasks/nope/versions/1/submit-review", json={})
        missing_source = await client.post(f"/api/content-bank/tasks/{uuid4()}/versions", json={"source_version_no":1})
        missing_body = await client.post(f"/api/content-bank/tasks/{uuid4()}/versions", json={})
    assert missing.status_code == missing_source.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert invalid.status_code == missing_body.status_code == 422


async def _create_search_task(client, catalog, *, title=None, statement="Нейтральное условие", source=None):
    data = payload(catalog)
    data["initial_version"].update(title=title, statement=statement, source=source)
    response = await client.post("/api/content-bank/tasks", json=data)
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize(("field", "value", "query"), [
    ("title", "Кинематическое движение", "кинематическое"),
    ("statement", "Рассчитайте скорость движения", "скорость"),
    ("source", "Сборник олимпиадных задач", "олимпиадный"),
])
async def test_searches_each_weighted_latest_version_field(catalog, field, value, query):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kwargs = {field: value}
        created = await _create_search_task(client, catalog, **kwargs)
        response = await client.get("/api/content-bank/tasks", params={"q": query})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["task_id"] == created["id"]


async def test_russian_morphology_matches_different_word_forms(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await _create_search_task(client, catalog, statement="Исследование движений нескольких тел")
        response = await client.get("/api/content-bank/tasks", params={"q": "движение"})
    assert [item["task_id"] for item in response.json()["items"]] == [created["id"]]


async def test_title_weight_ranks_above_statement_and_source(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        title = await _create_search_task(client, catalog, title="Импульс")
        statement = await _create_search_task(client, catalog, statement="Найдите импульс")
        source = await _create_search_task(client, catalog, source="Импульс")
        response = await client.get("/api/content-bank/tasks", params={"q": "импульс"})
    ids = [item["task_id"] for item in response.json()["items"]]
    assert ids.index(title["id"]) < ids.index(statement["id"])
    assert ids.index(statement["id"]) < ids.index(source["id"])


async def test_search_is_limited_to_latest_version(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await _create_search_task(client, catalog, statement="Исторический квантор")
        task_id = created["id"]
        async with async_session_factory() as session:
            await session.execute(text("UPDATE task_versions SET status='approved', approved_at=now(), approved_by=created_by WHERE task_id=:id"), {"id": task_id})
            await session.commit()
        cloned = await client.post(f"/api/content-bank/tasks/{task_id}/versions", json={"source_version_no": 1})
        assert cloned.status_code == 201
        async with async_session_factory() as session:
            await session.execute(text("UPDATE task_versions SET statement='Только актуальный интеграл' WHERE task_id=:id AND version_no=2"), {"id": task_id})
            await session.commit()
        historical = await client.get("/api/content-bank/tasks", params={"q": "квантор"})
        latest = await client.get("/api/content-bank/tasks", params={"q": "интеграл"})
    assert historical.json()["total"] == 0
    assert [item["task_id"] for item in latest.json()["items"]] == [task_id]


async def test_search_combines_with_subject_and_skill_without_duplicates(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await _create_search_task(client, catalog, statement="Общий поисковый маркер")
        await _create_search_task(client, catalog, statement="Общий поисковый маркер")
        response = await client.get("/api/content-bank/tasks", params={"q":"маркер", "subject_id":catalog["subject"], "skill_id":catalog["skill"]})
        wrong_subject = await client.get("/api/content-bank/tasks", params={"q":"маркер", "subject_id":uuid4()})
    ids = [item["task_id"] for item in response.json()["items"]]
    assert first["id"] in ids and response.json()["total"] == 2
    assert len(ids) == len(set(ids))
    assert wrong_subject.json()["total"] == 0


async def test_search_status_and_archived_semantics(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        active = await _create_search_task(client, catalog, statement="Статусный маркер")
        archived = await _create_search_task(client, catalog, statement="Статусный маркер")
        await client.post(f"/api/content-bank/tasks/{archived['id']}/archive")
        draft = await client.get("/api/content-bank/tasks", params={"q":"маркер", "status":"draft"})
        ordinary = await client.get("/api/content-bank/tasks", params={"q":"маркер"})
        archived_response = await client.get("/api/content-bank/tasks", params={"q":"маркер", "status":"archived"})
    assert [item["task_id"] for item in draft.json()["items"]] == [active["id"]]
    assert [item["task_id"] for item in ordinary.json()["items"]] == [active["id"]]
    assert [item["task_id"] for item in archived_response.json()["items"]] == [archived["id"]]


async def test_search_total_precedes_pagination(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(3): await _create_search_task(client, catalog, statement="Пагинационный маркер")
        response = await client.get("/api/content-bank/tasks", params={"q":"маркер", "offset":1, "limit":1})
    assert response.json()["total"] == 3
    assert len(response.json()["items"]) == 1


async def test_search_stable_tie_breaker_and_explicit_updated_at_sort(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await _create_search_task(client, catalog, statement="Равный ранг")
        second = await _create_search_task(client, catalog, statement="Равный ранг")
        async with async_session_factory() as session:
            await session.execute(text("UPDATE tasks SET updated_at='2020-01-01T00:00:00Z' WHERE id IN (:a,:b)"), {"a":first["id"], "b":second["id"]})
            await session.commit()
        tied = await client.get("/api/content-bank/tasks", params={"q":"равный"})
        async with async_session_factory() as session:
            await session.execute(text("UPDATE tasks SET updated_at=CASE WHEN id=:a THEN '2021-01-01T00:00:00Z'::timestamptz ELSE '2022-01-01T00:00:00Z'::timestamptz END WHERE id IN (:a,:b)"), {"a":first["id"], "b":second["id"]})
            await session.commit()
        explicit = await client.get("/api/content-bank/tasks", params={"q":"равный", "sort_by":"updated_at", "sort_order":"desc"})
    assert [x["task_id"] for x in tied.json()["items"]] == sorted([first["id"], second["id"]])
    assert [x["task_id"] for x in explicit.json()["items"]] == [second["id"], first["id"]]


async def test_empty_and_hostile_search_inputs_are_safe(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await _create_search_task(client, catalog)
        whitespace = await client.get("/api/content-bank/tasks", params={"q":"   "})
        hostile = await client.get("/api/content-bank/tasks", params={"q":'" ( - OR & !'})
        too_long = await client.get("/api/content-bank/tasks", params={"q":"я" * 201})
        relevance = await client.get("/api/content-bank/tasks", params={"sort_by":"relevance"})
    assert [x["task_id"] for x in whitespace.json()["items"]] == [created["id"]]
    assert hostile.status_code == 200
    assert too_long.status_code == 422 and too_long.json()["error"]["code"] == "validation_error"
    assert relevance.status_code == 422 and relevance.json()["error"]["code"] == "validation_error"


async def test_search_schema_has_generated_vector_and_gin_index(catalog):
    async with async_session_factory() as session:
        generated = await session.scalar(text("SELECT is_generated FROM information_schema.columns WHERE table_schema='public' AND table_name='task_versions' AND column_name='search_vector'"))
        indexdef = await session.scalar(text("SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='task_versions' AND indexname='ix_task_versions_search_vector_gin'"))
    assert generated == "ALWAYS"
    assert indexdef is not None and "USING gin" in indexdef

async def _force_display_timestamps_into_past(task_id, version_id):
    async with async_session_factory() as session:
        await session.execute(text("UPDATE tasks SET updated_at='2000-01-01T00:00:00Z' WHERE id=:task_id"), {"task_id":task_id})
        await session.execute(text("UPDATE task_versions SET updated_at='2000-01-01T00:00:00Z' WHERE id=:version_id"), {"version_id":version_id})
        await session.commit()


async def test_updated_at_lifecycle_and_read_model_consistency(catalog):
    past = "2000-01-01T00:00:00+00:00"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/content-bank/tasks", json=payload(catalog))
        task_id = created.json()["id"]
        version_id = created.json()["initial_version"]["id"]
        initial_card = (await client.get(f"/api/content-bank/tasks/{task_id}")).json()
        assert initial_card["updated_at"]
        assert initial_card["latest_version"]["updated_at"]

        await _force_display_timestamps_into_past(task_id, version_id)
        assert (await client.put(f"/api/content-bank/task-versions/{version_id}/methodology", json=methodology(catalog))).status_code == 200
        after_methodology = (await client.get(f"/api/content-bank/tasks/{task_id}")).json()
        assert after_methodology["updated_at"] != past
        assert after_methodology["latest_version"]["updated_at"] != past

        await _force_display_timestamps_into_past(task_id, version_id)
        assert (await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/submit-review", json={})).status_code == 200
        after_submit = (await client.get(f"/api/content-bank/tasks/{task_id}")).json()
        assert after_submit["updated_at"] != past
        assert after_submit["latest_version"]["updated_at"] != past

        await _force_display_timestamps_into_past(task_id, version_id)
        assert (await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/return-to-draft", json={"reason":"Проверка timestamp"})).status_code == 200
        after_return = (await client.get(f"/api/content-bank/tasks/{task_id}")).json()
        assert after_return["updated_at"] != past
        assert after_return["latest_version"]["updated_at"] != past

        await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/submit-review", json={})
        await _force_display_timestamps_into_past(task_id, version_id)
        assert (await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/approve", json={})).status_code == 200
        after_approve = (await client.get(f"/api/content-bank/tasks/{task_id}")).json()
        assert after_approve["updated_at"] != past
        assert after_approve["latest_version"]["updated_at"] != past

        previous_version_updated_at = after_approve["latest_version"]["updated_at"]
        async with async_session_factory() as session:
            await session.execute(text("UPDATE tasks SET updated_at='2000-01-01T00:00:00Z' WHERE id=:id"), {"id":task_id})
            await session.commit()
        assert (await client.post(f"/api/content-bank/tasks/{task_id}/versions", json={"source_version_no":1})).status_code == 201
        after_clone = (await client.get(f"/api/content-bank/tasks/{task_id}")).json()
        assert after_clone["updated_at"] != past
        assert after_clone["latest_version"]["updated_at"]
        async with async_session_factory() as session:
            unchanged_previous = await session.scalar(text("SELECT updated_at FROM task_versions WHERE id=:id"), {"id":version_id})
        assert unchanged_previous == datetime.fromisoformat(previous_version_updated_at.replace("Z", "+00:00"))

        latest_id = after_clone["latest_version"]["id"]
        await _force_display_timestamps_into_past(task_id, latest_id)
        assert (await client.post(f"/api/content-bank/tasks/{task_id}/archive")).status_code == 200
        after_archive = (await client.get(f"/api/content-bank/tasks/{task_id}")).json()
        assert after_archive["updated_at"] != past
        assert after_archive["latest_version"]["updated_at"] != past

        listed = (await client.get("/api/content-bank/tasks", params={"status":"archived"})).json()["items"][0]
        assert listed["updated_at"] == after_archive["updated_at"]


async def test_audit_schema_enum_constraints_and_indexes(catalog):
    async with async_session_factory() as session:
        enum_values = (await session.execute(text("SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid=enumtypid WHERE typname='audit_action' ORDER BY enumsortorder"))).scalars().all()
        indexes = set((await session.execute(text("SELECT indexname FROM pg_indexes WHERE tablename='audit_log'"))).scalars().all())
        constraints = set((await session.execute(text("SELECT conname FROM pg_constraint WHERE conrelid='audit_log'::regclass"))).scalars().all())
    assert enum_values == ["task_created", "methodology_updated", "submitted_for_review", "returned_to_draft", "version_approved", "version_created", "task_archived"]
    assert {"ix_audit_log_task_occurred_at", "ix_audit_log_task_action_occurred_at", "ix_audit_log_task_version_id"} <= indexes
    assert {"fk_audit_log_task", "fk_audit_log_task_version", "ck_audit_log_version_no_positive"} <= constraints


async def test_audit_read_filter_pagination_and_not_found(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/content-bank/tasks", json=payload(catalog))
        task_id = created.json()["id"]
        version_id = created.json()["initial_version"]["id"]
        await client.put(f"/api/content-bank/task-versions/{version_id}/methodology", json=methodology(catalog))
        full = await client.get(f"/api/content-bank/tasks/{task_id}/audit", params={"offset": 0, "limit": 1})
        filtered = await client.get(f"/api/content-bank/tasks/{task_id}/audit", params={"action": "task_created"})
        missing = await client.get(f"/api/content-bank/tasks/{uuid4()}/audit")
        invalid = await client.get(f"/api/content-bank/tasks/{task_id}/audit", params={"action": "unknown"})
    assert full.status_code == 200 and full.json()["total"] == 2 and len(full.json()["items"]) == 1
    assert filtered.json()["total"] == 1 and filtered.json()["items"][0]["action"] == "task_created"
    assert filtered.json()["items"][0]["actor_id"] == "00000000-0000-4000-8000-000000000001"
    assert missing.status_code == 404 and invalid.status_code == 422


async def test_archive_reason_is_atomic_and_idempotent_in_audit(catalog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/content-bank/tasks", json=payload(catalog)); task_id = created.json()["id"]
        failed = await client.post(f"/api/content-bank/tasks/{task_id}/versions/1/approve", json={})
        await client.post(f"/api/content-bank/tasks/{task_id}/archive", json={"reason": "obsolete"})
        await client.post(f"/api/content-bank/tasks/{task_id}/archive", json={"reason": "again"})
        audit = (await client.get(f"/api/content-bank/tasks/{task_id}/audit")).json()
    assert failed.status_code in {409, 422}
    archived = [item for item in audit["items"] if item["action"] == "task_archived"]
    assert len(archived) == 1 and archived[0]["reason"] == "obsolete"
    assert not any(item["action"] == "version_approved" for item in audit["items"])
