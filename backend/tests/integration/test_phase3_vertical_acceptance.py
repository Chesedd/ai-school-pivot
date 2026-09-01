"""Final real-PostgreSQL teacher -> student -> handoff Phase 3 acceptance."""
import hashlib
import os
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("TEST_DATABASE_URL", "")
if not URL:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Phase 3 vertical acceptance requires a database ending in _test")

from app.application.content_bank import ActorContext, ArchiveTaskService
from app.infrastructure.repository import SQLAlchemyUnitOfWork
from app.infrastructure.student_assessment_repository import AssessmentCheckingHandoffService
from app.main import app
import app.presentation.assessment_routes as teacher_routes
import app.presentation.student_assessment_routes as student_routes
from tests.integration.auth_helpers import (clear_principal_override, override_principal,
    student_principal, teacher_principal)

pytestmark = pytest.mark.asyncio
RAW = " 001,2300e2 "


@pytest_asyncio.fixture
async def vertical_database(monkeypatch):
    engine = create_async_engine(URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(teacher_routes, "async_session_factory", factory)
    monkeypatch.setattr(student_routes, "async_session_factory", factory)
    async with engine.begin() as connection:
        await connection.execute(text(
            "TRUNCATE assessment_audit_log, assessment_idempotency_keys, student_answers, "
            "student_submissions, assignment_participants, assignments, assessment_items, "
            "assessment_variants, assessments, students, class_groups, task_versions, tasks, "
            "topics, grades, subjects CASCADE"))
    try:
        yield engine, factory
    finally:
        clear_principal_override(app)
        await engine.dispose()


@pytest_asyncio.fixture
async def vertical_client(vertical_database):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://test") as client:
        yield client


async def approved_number_versions(engine, actor_id):
    subject, grade, topic = uuid4(), uuid4(), uuid4()
    tasks = [(uuid4(), uuid4()), (uuid4(), uuid4())]
    async with engine.begin() as connection:
        await connection.execute(text("INSERT INTO subjects(id,code,name,normalized_name) VALUES (:id,:code,'Vertical subject','vertical subject')"),
                                 {"id": subject, "code": f"vertical-{subject}"})
        await connection.execute(text("INSERT INTO grades(id,number,name,normalized_name) VALUES (:id,9,'9','9')"), {"id": grade})
        await connection.execute(text("INSERT INTO topics(id,subject_id,grade_id,code,name,normalized_name) "
            "VALUES (:id,:subject,:grade,:code,'Vertical topic','vertical topic')"),
            {"id": topic, "subject": subject, "grade": grade, "code": f"vertical-{topic}"})
        for index, (task_id, version_id) in enumerate(tasks, 1):
            await connection.execute(text("INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) "
                "VALUES (:task,:subject,:grade,:topic,:actor)"),
                {"task": task_id, "subject": subject, "grade": grade, "topic": topic, "actor": actor_id})
            await connection.execute(text("INSERT INTO task_versions(id,task_id,version_no,title,statement,"
                "task_type,answer_format,difficulty,status,created_by,approved_by,approved_at) VALUES "
                "(:version,:task,1,:title,:statement,'calculation','number',50,'approved',:actor,:actor,clock_timestamp())"),
                {"version": version_id, "task": task_id, "title": f"Number {index}",
                 "statement": f"Calculate {index}", "actor": actor_id})
    return tasks


async def test_phase3_teacher_student_historical_handoff_vertical(vertical_client, vertical_database):
    client = vertical_client
    engine, factory = vertical_database
    actor_id = UUID("00000000-0000-4000-8000-000000000001")
    student_id = UUID("00000000-0000-4000-8000-000000000002")
    student_account_id = uuid4()
    second_student_id = UUID("00000000-0000-4000-8000-000000000003")
    group_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(text("INSERT INTO class_groups(id,name,created_by) VALUES (:id,'9A vertical',:actor)"),
                                 {"id": group_id, "actor": actor_id})
        await connection.execute(text("INSERT INTO students(id,class_group_id,display_name) VALUES "
            "(:first,:group,'Pilot One'),(:second,:group,'Pilot Two')"),
            {"first": student_id, "second": second_student_id, "group": group_id})
    tasks = await approved_number_versions(engine, actor_id)
    override_principal(app, teacher_principal(actor_id))

    created = await client.post("/api/assessment-core/assessments",
                                json={"title": "Phase 3 vertical", "description": "Acceptance"})
    assert created.status_code == 201
    assessment_id = created.json()["id"]
    variants = []
    for name in ("A", "B"):
        response = await client.post(f"/api/assessment-core/assessments/{assessment_id}/variants", json={"name": name})
        assert response.status_code == 201
        variants.append(response.json())
    assert len(variants) == 2
    for variant, (_, version_id) in zip(variants, tasks):
        response = await client.post(
            f"/api/assessment-core/assessments/{assessment_id}/variants/{variant['id']}/items",
            json={"task_version_id": str(version_id), "points": "2.50"})
        assert response.status_code == 201

    before = (await client.get(f"/api/assessment-core/assessments/{assessment_id}")).json()
    original = [(v["id"], v["position"], [(i["id"], i["task_version_id"], i["position"], i["points"])
                for i in v["items"]]) for v in before["variants"]]
    publication = await client.post(f"/api/assessment-core/assessments/{assessment_id}/publish-and-assign", json={
        "class_group_id": str(group_id), "start_at": "2026-01-01T00:00:00Z",
        "due_at": "2099-01-01T00:00:00Z", "max_attempts": 2})
    assert publication.status_code == 201
    assignment_id = publication.json()["assignment"]["id"]
    assert publication.json()["assessment"]["status"] == "published"
    assert publication.json()["assignment"]["participant_count"] == 2
    reloaded = (await client.get(f"/api/assessment-core/assessments/{assessment_id}")).json()
    discovered = (await client.get(f"/api/assessment-core/assessments/{assessment_id}/assignments")).json()
    assert len(reloaded["variants"]) == 2 and discovered["total"] == 1
    assert discovered["items"][0]["id"] == assignment_id and discovered["items"][0]["participant_count"] == 2

    first_item = reloaded["variants"][0]["items"][0]
    frozen = await client.patch(
        f"/api/assessment-core/assessments/{assessment_id}/variants/{reloaded['variants'][0]['id']}/items/{first_item['id']}",
        json={"points": "3.00", "expected_updated_at": reloaded["updated_at"]})
    assert frozen.status_code == 409 and frozen.json()["error"]["code"] == "assessment_immutable"
    after = (await client.get(f"/api/assessment-core/assessments/{assessment_id}")).json()
    assert [(v["id"], v["position"], [(i["id"], i["task_version_id"], i["position"], i["points"])
            for i in v["items"]]) for v in after["variants"]] == original

    override_principal(app, student_principal(student_account_id, student_id))
    ordered = sorted([(v["position"], UUID(v["id"])) for v in reloaded["variants"]])
    digest = hashlib.sha256(UUID(assignment_id).bytes + student_id.bytes).digest()
    expected_variant = ordered[int.from_bytes(digest[:8], "big") % 2][1]
    started = await client.post(f"/api/assessment-core/student/assignments/{assignment_id}/attempts/start",
                                json={}, headers={"Idempotency-Key": "vertical-start"})
    assert started.status_code == 201 and UUID(started.json()["assigned_variant_id"]) == expected_variant
    replay_start = await client.post(f"/api/assessment-core/student/assignments/{assignment_id}/attempts/start",
                                     json={}, headers={"Idempotency-Key": "vertical-start"})
    for field in ("id", "attempt_no", "assigned_variant_id"):
        assert replay_start.json()[field] == started.json()[field]
    assert replay_start.status_code == 201

    submission_id = started.json()["id"]
    item = started.json()["items"][0]
    saved = await client.put(f"/api/assessment-core/student/attempts/{submission_id}/answers/{item['id']}",
                             json={"raw_answer": RAW, "expected_updated_at": None})
    assert saved.status_code == 201 and saved.json()["raw_answer"] == RAW
    assert saved.json()["normalized_answer"] == {"decimal": "123"}
    reopened = (await client.get(f"/api/assessment-core/student/attempts/{submission_id}")).json()
    assert reopened["answers"][0]["raw_answer"] == RAW
    assert reopened["answers"][0]["normalized_answer"] == {"decimal": "123"}

    submitted = await client.post(f"/api/assessment-core/student/attempts/{submission_id}/submit",
                                  json={}, headers={"Idempotency-Key": "vertical-submit"})
    assert submitted.status_code == 200 and submitted.json()["status"] == "submitted"
    replay_submit = await client.post(f"/api/assessment-core/student/attempts/{submission_id}/submit",
                                      json={}, headers={"Idempotency-Key": "vertical-submit"})
    assert replay_submit.status_code == 200
    assert replay_submit.json()["id"] == submission_id
    assert replay_submit.json()["submitted_at"] == submitted.json()["submitted_at"]

    detail = (await client.get(f"/api/assessment-core/student/assignments/{assignment_id}")).json()
    assert detail["submitted_attempt_count"] == len(detail["submitted_attempts"]) == 1
    assert detail["submitted_attempts"][0] == {"id": submission_id, "attempt_no": 1,
                                               "submitted_at": submitted.json()["submitted_at"]}
    assert detail["current_draft_attempt_id"] is None
    historical = await client.get(f"/api/assessment-core/student/attempts/{detail['submitted_attempts'][0]['id']}")
    assert historical.status_code == 200 and historical.json()["status"] == "submitted"

    handoff = await AssessmentCheckingHandoffService(factory).get(UUID(submission_id))
    projected = next(x for x in handoff.items if x.assessment_item_id == UUID(item["id"]))
    assert projected.task_version_id == UUID(item["task_version_id"])
    assert projected.points == Decimal("2.50") and projected.answer_format == "number"
    assert projected.raw_answer == RAW and projected.normalized_answer == {"decimal": "123"}

    archived_task = next(task for task, version in tasks if version == UUID(item["task_version_id"]))
    await ArchiveTaskService(SQLAlchemyUnitOfWork(factory)).archive(archived_task, ActorContext(actor_id))
    archived_read = (await client.get(f"/api/assessment-core/student/attempts/{submission_id}")).json()
    assert archived_read["answers"][0]["raw_answer"] == RAW
    assert archived_read["answers"][0]["normalized_answer"] == {"decimal": "123"}
    assert archived_read["items"][0]["task_version_id"] == item["task_version_id"]
    assert archived_read["items"][0]["answer_format"] == "number"
    archived_handoff = await AssessmentCheckingHandoffService(factory).get(UUID(submission_id))
    assert archived_handoff == handoff

    override_principal(app, teacher_principal(actor_id))
    closed = await client.post(f"/api/assessment-core/assignments/{assignment_id}/close", json={})
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    assert (await client.get(f"/api/assessment-core/assignments/{assignment_id}")).json()["status"] == "closed"
    override_principal(app, student_principal(student_account_id, student_id))
    assert (await client.get(f"/api/assessment-core/student/attempts/{submission_id}")).status_code == 200
    assert await AssessmentCheckingHandoffService(factory).get(UUID(submission_id)) == handoff
    blocked = await client.post(f"/api/assessment-core/student/assignments/{assignment_id}/attempts/start",
                                json={}, headers={"Idempotency-Key": "vertical-after-close"})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "assignment_closed"

    async with engine.connect() as connection:
        rows = (await connection.execute(text("SELECT event_type, details::text FROM assessment_audit_log"))).all()
        event_names = [row.event_type for row in rows]
        for event in ("assessment_published", "assignment_created", "variant_assigned", "submission_started",
                      "answer_saved", "submission_submitted", "assignment_closed"):
            assert event_names.count(event) == 1
        assert all(RAW not in row[1] for row in rows)
        assert await connection.scalar(text("SELECT count(*) FROM student_submissions")) == 1
