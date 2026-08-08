"""Real PostgreSQL API, atomicity, CAS, and locking regression tests."""
import asyncio
import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

URL = os.environ.get("TEST_DATABASE_URL", "")
if not URL:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Assessment API tests require a database ending in _test")

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.assessments import AssessmentService
from app.application.content_bank import ActorContext
from app.infrastructure.assessment_models import Assessment, AssessmentAuditLog, AssessmentVariant
from app.infrastructure.assessment_repository import SQLAlchemyAssessmentUnitOfWork
from app.main import app
import app.presentation.assessment_routes as assessment_routes

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def database(monkeypatch):
    engine = create_async_engine(URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(assessment_routes, "async_session_factory", factory)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE assessment_audit_log, assessment_idempotency_keys, student_answers, student_submissions, assignment_participants, assignments, assessment_items, assessment_variants, assessments, students, class_groups CASCADE"))
    try:
        yield engine, factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(database):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as value:
        yield value


async def create(client, title="Работа"):
    response = await client.post("/api/assessment-core/assessments", json={"title": title, "created_by": str(uuid4())})
    assert response.status_code == 422  # actor fields are forbidden
    response = await client.post("/api/assessment-core/assessments", json={"title": title, "description": None})
    assert response.status_code == 201
    assert response.headers["location"] == f"/api/assessment-core/assessments/{response.json()['id']}"
    assert response.json()["variants"] == []
    return response.json()


async def test_assessment_api_lifecycle_ownership_immutability_and_audit(client, database):
    engine, _ = database
    first = await create(client, "Первая")
    second = await create(client, "Вторая")
    page = (await client.get("/api/assessment-core/assessments", params={"status": "draft"})).json()
    assert page["total"] == 2 and {item["id"] for item in page["items"]} == {first["id"], second["id"]}
    assert (await client.get(f"/api/assessment-core/assessments/{first['id']}")).json()["variants"] == []

    patched = await client.patch(f"/api/assessment-core/assessments/{first['id']}", json={"title": "Обновлена", "expected_updated_at": first["updated_at"]})
    assert patched.status_code == 200 and patched.json()["title"] == "Обновлена"
    before_variant = patched.json()["updated_at"]
    one = await client.post(f"/api/assessment-core/assessments/{first['id']}/variants", json={"name": "A"})
    after_create = (await client.get(f"/api/assessment-core/assessments/{first['id']}")).json()["updated_at"]
    assert after_create > before_variant
    two = await client.post(f"/api/assessment-core/assessments/{first['id']}/variants", json={"name": "B"})
    assert [one.json()["position"], two.json()["position"]] == [1, 2]
    duplicate = await client.post(f"/api/assessment-core/assessments/{first['id']}/variants", json={"name": "A"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "concurrent_conflict"
    foreign = await client.delete(f"/api/assessment-core/assessments/{second['id']}/variants/{one.json()['id']}")
    assert foreign.status_code == 404 and foreign.json()["error"]["code"] == "variant_not_found"
    assert (await client.delete(f"/api/assessment-core/assessments/{first['id']}/variants/{one.json()['id']}")).status_code == 204
    detail = (await client.get(f"/api/assessment-core/assessments/{first['id']}")).json()
    assert detail["updated_at"] > after_create
    assert [(value["name"], value["position"]) for value in detail["variants"]] == [("B", 1)]

    async with engine.begin() as connection:
        await connection.execute(text("UPDATE assessments SET status='published', published_at=clock_timestamp(), published_by=created_by WHERE id=:id"), {"id": first["id"]})
    for response in (
        await client.patch(f"/api/assessment-core/assessments/{first['id']}", json={"title": "Нет", "expected_updated_at": detail["updated_at"]}),
        await client.post(f"/api/assessment-core/assessments/{first['id']}/variants", json={"name": "C"}),
        await client.delete(f"/api/assessment-core/assessments/{first['id']}/variants/{two.json()['id']}"),
    ):
        assert response.status_code == 409 and response.json()["error"]["code"] == "assessment_immutable"
    async with engine.connect() as connection:
        events = (await connection.execute(text("SELECT event_type FROM assessment_audit_log WHERE aggregate_id=:id ORDER BY occurred_at,id"), {"id": first["id"]})).scalars().all()
    assert events == ["assessment_created", "assessment_metadata_updated", "variant_created", "variant_created", "variant_deleted"]


async def test_patch_atomic_cas_loser_has_no_audit(client, database):
    engine, _ = database
    row = await create(client)
    payload = {"description": "winner", "expected_updated_at": row["updated_at"]}
    winner = await client.patch(f"/api/assessment-core/assessments/{row['id']}", json=payload)
    loser = await client.patch(f"/api/assessment-core/assessments/{row['id']}", json={**payload, "description": "loser"})
    assert winner.status_code == 200
    assert loser.status_code == 409 and loser.json()["error"]["code"] == "concurrent_conflict"
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM assessment_audit_log WHERE aggregate_id=:id AND event_type='assessment_metadata_updated'"), {"id": row["id"]}) == 1


async def test_concurrent_variant_creation_serializes_positions_and_audit(database):
    engine, factory = database
    actor = ActorContext(uuid4())
    async with factory() as session:
        row = Assessment(title="Concurrent", created_by=actor.actor_id)
        session.add(row); await session.commit(); await session.refresh(row)
        assessment_id = row.id

    ready = 0
    gate = asyncio.Event()
    lock = asyncio.Lock()
    async def command(name):
        nonlocal ready
        async with lock:
            ready += 1
            if ready == 2: gate.set()
        await gate.wait()
        return await AssessmentService(SQLAlchemyAssessmentUnitOfWork(factory)).create_variant(assessment_id, name, actor)

    left, right = await asyncio.gather(command("A"), command("B"))
    assert {left.position, right.position} == {1, 2}
    async with factory() as session:
        positions = (await session.scalars(select(AssessmentVariant.position).where(AssessmentVariant.assessment_id == assessment_id).order_by(AssessmentVariant.position))).all()
        audits = (await session.scalars(select(AssessmentAuditLog).where(AssessmentAuditLog.aggregate_id == assessment_id))).all()
    assert positions == [1, 2] and len(audits) == 2
