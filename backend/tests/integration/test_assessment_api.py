"""Real PostgreSQL API, atomicity, CAS, and locking regression tests."""
import asyncio
import os
from decimal import Decimal
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

from app.application.assessments import AddAssessmentItemCommand, AssessmentError, AssessmentService
from app.application.content_bank import ActorContext, ArchiveTaskService
from app.infrastructure.assessment_models import Assessment, AssessmentAuditLog, AssessmentVariant
from app.infrastructure.assessment_repository import SQLAlchemyAssessmentUnitOfWork, SQLAlchemyContentBankReadPort
from app.infrastructure.repository import SQLAlchemyUnitOfWork
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


async def content_version(engine, status="approved", archived=False):
    values = {key: uuid4() for key in ("actor", "subject", "grade", "topic", "task", "version")}
    async with engine.begin() as connection:
        catalog = (await connection.execute(text("SELECT s.id,g.id,t.id FROM topics t JOIN subjects s ON s.id=t.subject_id JOIN grades g ON g.id=t.grade_id LIMIT 1"))).one_or_none()
        if catalog is None:
            await connection.execute(text("INSERT INTO subjects(id,code,name) VALUES (:subject,:code,'Assessment composition')"),
                                     {**values, "code": f"assessment-{values['subject']}"})
            await connection.execute(text("INSERT INTO grades(id,number,name) VALUES (:grade,11,:name)"),
                                     {**values, "name": str(values["grade"])})
            await connection.execute(text("INSERT INTO topics(id,subject_id,grade_id,code,name) VALUES (:topic,:subject,:grade,:code,'Topic')"),
                                     {**values, "code": str(values["topic"])})
        else:
            values.update(subject=catalog[0], grade=catalog[1], topic=catalog[2])
        await connection.execute(text("INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by,archived_at) VALUES (:task,:subject,:grade,:topic,:actor,CASE WHEN :archived THEN clock_timestamp() END)"),
                                 {**values, "archived": archived})
        await connection.execute(text("INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:version,:task,1,'Statement','problem','short_text',50,:status,:actor)"),
                                 {**values, "status": status})
    return values


async def assert_postgres_lock_wait(engine, pid):
    """Observe a real server-side lock wait without timing sleeps."""
    for _ in range(200):
        async with engine.connect() as connection:
            waiting = await connection.scalar(text(
                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid})
        if waiting:
            return
    pytest.fail(f"PostgreSQL backend {pid} was never observed waiting on a lock")


class PausingArchiveUnitOfWork(SQLAlchemyUnitOfWork):
    """Test-only wrapper around the production archive repository primitive."""
    def __init__(self, factory, locked, release, started=None):
        super().__init__(factory); self.locked = locked; self.release = release; self.started = started

    async def __aenter__(self):
        await super().__aenter__()
        delegate = self.repository
        owner = self
        class RepositoryProxy:
            def __getattr__(self, name): return getattr(delegate, name)
            async def archive_task_versions(self, task_id, archived_at):
                if owner.started is not None:
                    owner.pid = await delegate.session.scalar(text("SELECT pg_backend_pid()"))
                    owner.started.set()
                result = await delegate.archive_task_versions(task_id, archived_at)
                owner.locked.set()
                await owner.release.wait()
                return result
        self.repository = RepositoryProxy()
        return self


class PausingAddUnitOfWork(SQLAlchemyAssessmentUnitOfWork):
    """Test-only wrapper around the production eligibility adapter."""
    def __init__(self, factory, locked=None, release=None, started=None):
        super().__init__(factory); self.locked = locked; self.release = release; self.started = started

    async def __aenter__(self):
        await super().__aenter__()
        delegate = SQLAlchemyContentBankReadPort(self.session)
        owner = self
        class PortProxy:
            async def lock_new_usage(self, version_id):
                owner.pid = await owner.session.scalar(text("SELECT pg_backend_pid()"))
                if owner.started is not None: owner.started.set()
                result = await delegate.lock_new_usage(version_id)
                if owner.locked is not None: owner.locked.set()
                if owner.release is not None: await owner.release.wait()
                return result
        self.content_bank = PortProxy()
        return self


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


async def test_composition_api_validation_reorder_points_delete_and_history(client, database):
    engine, _ = database
    assessment = await create(client)
    variant = (await client.post(f"/api/assessment-core/assessments/{assessment['id']}/variants", json={"name": "A"})).json()
    approved = await content_version(engine)
    invalid = await content_version(engine, "review")
    archived = await content_version(engine, archived=True)
    path = f"/api/assessment-core/assessments/{assessment['id']}/variants/{variant['id']}"
    for version in (uuid4(), invalid["version"], archived["version"]):
        response = await client.post(f"{path}/items", json={"task_version_id": str(version), "points": "1.00"})
        assert response.status_code == 409 and response.json()["error"]["code"] == "invalid_task_version"
    for points in ("0", "-1", "1.234", "1000000.00", "NaN", "Infinity"):
        assert (await client.post(f"{path}/items", json={"task_version_id": str(approved["version"]), "points": points})).status_code == 422
    first = await client.post(f"{path}/items", json={"task_version_id": str(approved["version"]), "points": "2.50"})
    assert first.status_code == 201 and first.json()["points"] == "2.50"
    assert first.headers["location"] == f"{path}/items/{first.json()['id']}"
    duplicate = await client.post(f"{path}/items", json={"task_version_id": str(approved["version"]), "points": "1"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "concurrent_conflict"
    second_version = await content_version(engine)
    second = await client.post(f"{path}/items", json={"task_version_id": str(second_version["version"]), "points": "1"})
    assert second.status_code == 201 and second.json()["position"] == 2
    token = (await client.get(f"/api/assessment-core/assessments/{assessment['id']}")).json()["updated_at"]
    for malformed in (
        [first.json()["id"], first.json()["id"]],
        [first.json()["id"]],
        [first.json()["id"], second.json()["id"], str(uuid4())],
    ):
        response = await client.put(f"{path}/item-order", json={"item_ids": malformed, "expected_updated_at": token})
        assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    reordered = await client.put(f"{path}/item-order", json={"item_ids": [second.json()["id"], first.json()["id"]], "expected_updated_at": token})
    assert reordered.status_code == 200 and [item["position"] for item in reordered.json()["items"]] == [1, 2]
    stale = await client.patch(f"{path}/items/{first.json()['id']}", json={"points": "3.00", "expected_updated_at": token})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "concurrent_conflict"
    fresh = (await client.get(f"/api/assessment-core/assessments/{assessment['id']}")).json()["updated_at"]
    changed = await client.patch(f"{path}/items/{first.json()['id']}", json={"points": "3.00", "expected_updated_at": fresh})
    assert changed.status_code == 200 and changed.json()["points"] == "3.00"
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE tasks SET archived_at=clock_timestamp() WHERE id=:id"), {"id": approved["task"]})
        await connection.execute(text("UPDATE task_versions SET status='archived' WHERE id=:id"), {"id": approved["version"]})
    detail = (await client.get(f"/api/assessment-core/assessments/{assessment['id']}")).json()
    assert any(item["id"] == first.json()["id"] for item in detail["variants"][0]["items"])
    assert (await client.delete(f"{path}/items/{first.json()['id']}")).status_code == 204
    final = (await client.get(f"/api/assessment-core/assessments/{assessment['id']}")).json()
    assert [item["position"] for item in final["variants"][0]["items"]] == [1]
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE assessments SET status='published', published_at=clock_timestamp(), published_by=created_by WHERE id=:id"), {"id": assessment["id"]})
    published_token = final["updated_at"]
    for response in (
        await client.post(f"{path}/items", json={"task_version_id": str(uuid4()), "points": "1.00"}),
        await client.delete(f"{path}/items/{second.json()['id']}"),
        await client.put(f"{path}/item-order", json={"item_ids": [second.json()["id"]], "expected_updated_at": published_token}),
        await client.patch(f"{path}/items/{second.json()['id']}", json={"points": "2.00", "expected_updated_at": published_token}),
    ):
        assert response.status_code == 409 and response.json()["error"]["code"] == "assessment_immutable"
    async with engine.connect() as connection:
        events = (await connection.execute(text("SELECT event_type FROM assessment_audit_log WHERE aggregate_id=:id"), {"id": assessment["id"]})).scalars().all()
    assert events.count("item_added") == 2 and events.count("items_reordered") == 1
    assert events.count("item_points_changed") == 1 and events.count("item_removed") == 1


async def test_concurrent_item_adds_serialize_and_duplicate_is_normative(database):
    engine, factory = database
    actor = ActorContext(uuid4())
    left_version = await content_version(engine)
    right_version = await content_version(engine)
    async with factory() as session:
        assessment = Assessment(title="Concurrent items", created_by=actor.actor_id)
        session.add(assessment); await session.flush()
        variant = AssessmentVariant(assessment_id=assessment.id, name="A", position=1)
        session.add(variant); await session.commit()
        assessment_id, variant_id = assessment.id, variant.id

    gate = asyncio.Event(); ready = 0; guard = asyncio.Lock()
    async def add(version_id):
        nonlocal ready
        async with guard:
            ready += 1
            if ready == 2: gate.set()
        await gate.wait()
        return await AssessmentService(SQLAlchemyAssessmentUnitOfWork(factory)).add_item(
            AddAssessmentItemCommand(assessment_id, variant_id, version_id, Decimal("1.00")), actor)

    first, second = await asyncio.gather(add(left_version["version"]), add(right_version["version"]))
    assert {first.position, second.position} == {1, 2}
    duplicate_version = await content_version(engine)
    duplicate_results = await asyncio.gather(add(duplicate_version["version"]), add(duplicate_version["version"]), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in duplicate_results) == 1
    assert sum(isinstance(result, AssessmentError) and result.code == "concurrent_conflict"
               for result in duplicate_results) == 1
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM assessment_items WHERE variant_id=:id"), {"id": variant_id}) == 3
        assert await connection.scalar(text("SELECT count(*) FROM assessment_audit_log WHERE aggregate_id=:id AND event_type='item_added'"), {"id": assessment_id}) == 3


async def test_archive_wins_add_waits_then_revalidates_without_partial_state(database):
    engine, factory = database
    actor = ActorContext(uuid4()); content = await content_version(engine)
    async with factory() as session:
        assessment = Assessment(title="Archive wins", created_by=actor.actor_id)
        session.add(assessment); await session.flush()
        variant = AssessmentVariant(assessment_id=assessment.id, name="A", position=1)
        session.add(variant); await session.commit(); await session.refresh(assessment)
        assessment_id, variant_id, initial_updated_at = assessment.id, variant.id, assessment.updated_at

    archive_locked = asyncio.Event(); release_archive = asyncio.Event()
    archive_uow = PausingArchiveUnitOfWork(factory, archive_locked, release_archive)
    archive = asyncio.create_task(ArchiveTaskService(archive_uow).archive(content["task"], actor))
    await asyncio.wait_for(archive_locked.wait(), timeout=5)

    add_started = asyncio.Event()
    add_uow = PausingAddUnitOfWork(factory, started=add_started)
    add = asyncio.create_task(AssessmentService(add_uow).add_item(AddAssessmentItemCommand(
        assessment_id, variant_id, content["version"], Decimal("1.00")), actor))
    await asyncio.wait_for(add_started.wait(), timeout=5)
    await assert_postgres_lock_wait(engine, add_uow.pid)
    assert not add.done()

    release_archive.set()
    await asyncio.wait_for(archive, timeout=5)
    with pytest.raises(AssessmentError) as error:
        await asyncio.wait_for(add, timeout=5)
    assert error.value.code == "invalid_task_version"
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM assessment_items WHERE variant_id=:id"), {"id": variant_id}) == 0
        assert await connection.scalar(text("SELECT count(*) FROM assessment_audit_log WHERE aggregate_id=:id AND event_type='item_added'"), {"id": assessment_id}) == 0
        assert await connection.scalar(text("SELECT updated_at=:value FROM assessments WHERE id=:id"), {"id": assessment_id, "value": initial_updated_at})


async def test_add_wins_archive_waits_then_historical_item_survives(client, database):
    engine, factory = database
    actor = ActorContext(uuid4()); content = await content_version(engine)
    assessment = await create(client, "Add wins")
    variant = (await client.post(f"/api/assessment-core/assessments/{assessment['id']}/variants", json={"name": "A"})).json()

    add_locked = asyncio.Event(); release_add = asyncio.Event()
    add_uow = PausingAddUnitOfWork(factory, locked=add_locked, release=release_add)
    add = asyncio.create_task(AssessmentService(add_uow).add_item(AddAssessmentItemCommand(
        assessment["id"], variant["id"], content["version"], Decimal("1.00")), actor))
    await asyncio.wait_for(add_locked.wait(), timeout=5)

    archive_started = asyncio.Event(); archive_reached_locks = asyncio.Event(); release_archive = asyncio.Event()
    archive_uow = PausingArchiveUnitOfWork(
        factory, archive_reached_locks, release_archive, started=archive_started)
    archive = asyncio.create_task(ArchiveTaskService(archive_uow).archive(content["task"], actor))
    await asyncio.wait_for(archive_started.wait(), timeout=5)
    await assert_postgres_lock_wait(engine, archive_uow.pid)
    assert not archive.done()

    release_add.set()
    item = await asyncio.wait_for(add, timeout=5)
    await asyncio.wait_for(archive_reached_locks.wait(), timeout=5)
    release_archive.set()
    result = await asyncio.wait_for(archive, timeout=5)
    assert result.changed

    detail = (await client.get(f"/api/assessment-core/assessments/{assessment['id']}")).json()
    historical = detail["variants"][0]["items"]
    assert [(value["id"], value["task_version_id"]) for value in historical] == [
        (str(item.id), str(content["version"]))]
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM assessment_items WHERE id=:id AND task_version_id=:version"), {"id": item.id, "version": content["version"]}) == 1
        assert await connection.scalar(text("SELECT count(*) FROM assessment_audit_log WHERE aggregate_id=:id AND event_type='item_added'"), {"id": assessment["id"]}) == 1
