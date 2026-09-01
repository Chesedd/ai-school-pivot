"""Real PostgreSQL API, atomicity, CAS, and locking regression tests."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

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

from app.application.assessments import (AddAssessmentItemCommand, AssessmentError, AssessmentService,
    ChangeAssessmentItemPointsCommand, PublishAssessmentCommand)
from app.application.content_bank import ActorContext, ArchiveTaskService
from app.infrastructure.assessment_models import (Assignment, AssignmentParticipant, Assessment,
    AssessmentAuditLog, AssessmentVariant, ClassGroup, Student)
from app.infrastructure.assessment_repository import SQLAlchemyAssessmentUnitOfWork, SQLAlchemyContentBankReadPort
from app.infrastructure.repository import SQLAlchemyUnitOfWork
from app.main import app
import app.presentation.assessment_routes as assessment_routes
from tests.integration.auth_helpers import clear_principal_override, override_principal, teacher_principal

TEACHER_ID = UUID("00000000-0000-4000-8000-000000000001")

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def database(monkeypatch):
    engine = create_async_engine(URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(assessment_routes, "async_session_factory", factory)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE assessment_audit_log, assessment_idempotency_keys, student_answers, student_submissions, assignment_participants, assignments, assessment_items, assessment_variants, assessments, students, class_groups CASCADE"))
    override_principal(app, teacher_principal(TEACHER_ID))
    try:
        yield engine, factory
    finally:
        clear_principal_override(app)
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


async def test_teacher_read_catalogues_are_ordered_scoped_and_private(client, database):
    engine, factory = database
    actor_id = TEACHER_ID
    group_a_id, group_a2_id, group_b_id, archived_id = sorted([uuid4(), uuid4(), uuid4(), uuid4()])
    assessment_id, foreign_assessment_id = uuid4(), uuid4()
    first_assignment_id, second_assignment_id, foreign_assignment_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:
        await connection.execute(text("INSERT INTO class_groups(id,name,external_ref,created_by,archived_at) VALUES "
            "(:a,'A','private-a',:actor,NULL),(:a2,'A','private-a2',:actor,NULL),"
            "(:b,'B','private-b',:actor,NULL),(:archived,'0 archived','private-x',:actor,clock_timestamp())"),
            {"a": group_a_id, "a2": group_a2_id, "b": group_b_id, "archived": archived_id, "actor": actor_id})
        students = [{"id": uuid4(), "group": group_a_id, "name": "Active A", "archived": None},
                    {"id": uuid4(), "group": group_a_id, "name": "Archived A", "archived": now},
                    {"id": uuid4(), "group": group_b_id, "name": "Active B1", "archived": None},
                    {"id": uuid4(), "group": group_b_id, "name": "Active B2", "archived": None}]
        for student in students:
            await connection.execute(text("INSERT INTO students(id,class_group_id,display_name,archived_at) "
                "VALUES (:id,:group,:name,:archived)"), student)
        await connection.execute(text("INSERT INTO assessments(id,title,created_by) VALUES "
            "(:assessment,'Own',:actor),(:foreign,'Foreign',:actor)"),
            {"assessment": assessment_id, "foreign": foreign_assessment_id, "actor": actor_id})
        for assignment_id, target_assessment, created_at in (
            (second_assignment_id, assessment_id, now + timedelta(seconds=1)),
            (first_assignment_id, assessment_id, now),
            (foreign_assignment_id, foreign_assessment_id, now)):
            await connection.execute(text("INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,"
                "created_at,created_by) VALUES (:id,:assessment,:group,:start,:due,:created,:actor)"),
                {"id": assignment_id, "assessment": target_assessment, "group": group_a_id,
                 "start": now, "due": now + timedelta(days=1), "created": created_at, "actor": actor_id})
        await connection.execute(text("INSERT INTO assignment_participants(assignment_id,student_id) VALUES "
            "(:first,:student),(:second,:student)"),
            {"first": first_assignment_id, "second": second_assignment_id, "student": students[0]["id"]})

    groups = (await client.get("/api/assessment-core/class-groups?offset=0&limit=20")).json()
    assert [(row["name"], UUID(row["id"])) for row in groups["items"]] == [
        ("A", group_a_id), ("A", group_a2_id), ("B", group_b_id)]
    assert [row["active_student_count"] for row in groups["items"]] == [1, 0, 2]
    assert str(archived_id) not in str(groups)
    for row in groups["items"]:
        assert set(row) == {"id", "name", "active_student_count"}
        assert "external_ref" not in row and "students" not in row

    assignments = (await client.get(
        f"/api/assessment-core/assessments/{assessment_id}/assignments?offset=0&limit=20")).json()
    assert [row["id"] for row in assignments["items"]] == [str(first_assignment_id), str(second_assignment_id)]
    assert all(row["assessment_id"] == str(assessment_id) for row in assignments["items"])
    assert str(foreign_assignment_id) not in str(assignments)
    assert all(row["class_group_name"] == "A" and row["participant_count"] == 1
               for row in assignments["items"])
    for row in assignments["items"]:
        assert "participant_ids" not in row and "answers" not in row


async def content_version(engine, status="approved", archived=False):
    values = {key: uuid4() for key in ("actor", "subject", "grade", "topic", "task", "version")}
    values["actor"] = TEACHER_ID
    async with engine.begin() as connection:
        catalog = (await connection.execute(text("SELECT s.id,g.id,t.id FROM topics t JOIN subjects s ON s.id=t.subject_id JOIN grades g ON g.id=t.grade_id LIMIT 1"))).one_or_none()
        if catalog is None:
            await connection.execute(text("INSERT INTO subjects(id,code,name,normalized_name) VALUES (:subject,:code,'Assessment composition','assessment composition')"),
                                     {**values, "code": f"assessment-{values['subject']}"})
            await connection.execute(text("INSERT INTO grades(id,number,name,normalized_name) VALUES (:grade,11,:name,:normalized_name)"),
                                     {**values, "name": str(values["grade"]), "normalized_name": str(values["grade"])})
            await connection.execute(text("INSERT INTO topics(id,subject_id,grade_id,code,name,normalized_name) VALUES (:topic,:subject,:grade,:code,'Topic','topic')"),
                                     {**values, "code": str(values["topic"])})
        else:
            values.update(subject=catalog[0], grade=catalog[1], topic=catalog[2])
        await connection.execute(text("INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by,archived_at) VALUES (:task,:subject,:grade,:topic,:actor,CASE WHEN :archived THEN clock_timestamp() END)"),
                                 {**values, "archived": archived})
        await connection.execute(text("INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:version,:task,1,'Statement','problem','short_text',50,:status,:actor)"),
                                 {**values, "status": status})
    return values


async def test_publish_assignment_snapshot_get_close_and_no_partial_failures(client, database):
    engine, factory = database
    assessment = await create(client, "Публикация")
    variant = (await client.post(
        f"/api/assessment-core/assessments/{assessment['id']}/variants", json={"name": "A"})).json()
    version = await content_version(engine)
    assert (await client.post(
        f"/api/assessment-core/assessments/{assessment['id']}/variants/{variant['id']}/items",
        json={"task_version_id": str(version["version"]), "points": "2.00"})).status_code == 201
    actor_id = TEACHER_ID
    async with factory() as session:
        group = ClassGroup(name="9А", created_by=actor_id)
        session.add(group); await session.flush()
        active = Student(class_group_id=group.id, display_name="Active A")
        second_active = Student(class_group_id=group.id, display_name="Active B")
        archived = Student(class_group_id=group.id, display_name="Archived", archived_at=await session.scalar(text("SELECT clock_timestamp()")))
        session.add_all([active, second_active, archived]); await session.commit()
        group_id, active_id, second_active_id = group.id, active.id, second_active.id
    payload = {"class_group_id": str(group_id), "start_at": "2026-08-01T09:00:00Z",
               "due_at": "2099-09-01T10:00:00Z", "max_attempts": 2}
    groups = await client.get("/api/assessment-core/class-groups?offset=0&limit=20")
    assert groups.status_code == 200
    assert groups.json()["items"] == [{"id": str(group_id), "name": "9А", "active_student_count": 2}]
    assert not ({"external_ref", "students", "participant_ids"} & groups.json()["items"][0].keys())
    published = await client.post(
        f"/api/assessment-core/assessments/{assessment['id']}/publish-and-assign", json=payload)
    assert published.status_code == 201
    body = published.json(); assignment_id = body["assignment"]["id"]
    assert published.headers["location"] == f"/api/assessment-core/assignments/{assignment_id}"
    assert body["assessment"]["status"] == "published"
    assert body["assignment"]["participant_count"] == 2
    assert body["assignment"]["participant_ids"] == sorted([str(active_id), str(second_active_id)])
    discovered = await client.get(f"/api/assessment-core/assessments/{assessment['id']}/assignments")
    assert discovered.status_code == 200
    assert discovered.json()["items"][0] == {
        "id": assignment_id, "assessment_id": assessment["id"], "class_group_id": str(group_id),
        "class_group_name": "9А", "status": "open", "start_at": "2026-08-01T09:00:00Z",
        "due_at": "2099-09-01T10:00:00Z", "max_attempts": 2, "participant_count": 2,
        "created_at": body["assignment"]["created_at"], "closed_at": None}
    assert "participant_ids" not in discovered.json()["items"][0]
    async with factory() as session:
        participant = await session.scalar(select(AssignmentParticipant).where(
            AssignmentParticipant.assignment_id == UUID(assignment_id)))
        assert participant.assigned_variant_id is None and participant.variant_assigned_at is None
        active_row = await session.get(Student, active_id)
        active_row.archived_at = await session.scalar(text("SELECT clock_timestamp()"))
        session.add(Student(class_group_id=group_id, display_name="Later")); await session.commit()
    summary = await client.get(f"/api/assessment-core/assignments/{assignment_id}")
    assert summary.status_code == 200
    assert summary.json()["participant_ids"] == sorted([str(active_id), str(second_active_id)])
    duplicate = await client.post(
        f"/api/assessment-core/assessments/{assessment['id']}/publish-and-assign", json=payload)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "assessment_immutable"
    closed = await client.post(f"/api/assessment-core/assignments/{assignment_id}/close", json={})
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    assert (await client.get(f"/api/assessment-core/assessments/{assessment['id']}/assignments")).json()["items"][0]["status"] == "closed"
    repeated = await client.post(f"/api/assessment-core/assignments/{assignment_id}/close", json={})
    assert repeated.status_code == 409 and repeated.json()["error"]["code"] == "invalid_status_transition"
    async with engine.connect() as connection:
        events = (await connection.execute(text(
            "SELECT event_type FROM assessment_audit_log WHERE event_type IN "
            "('assessment_published','assignment_created','assignment_closed') ORDER BY occurred_at,id"))).scalars().all()
        assert events == ["assessment_published", "assignment_created", "assignment_closed"]


async def test_publication_readiness_and_strict_time_validation(client, database):
    engine, factory = database
    assessment = await create(client)
    payload = {"class_group_id": str(uuid4()), "start_at": "2026-08-01T09:00:00Z",
               "due_at": "2099-09-01T10:00:00Z", "max_attempts": 1}
    response = await client.post(
        f"/api/assessment-core/assessments/{assessment['id']}/publish-and-assign", json=payload)
    assert response.status_code == 422 and response.json()["error"]["details"][0]["code"] == "no_variants"
    for changes in ({"start_at": "2026-08-01T09:00:00"}, {"due_at": "2099-09-01T10:00:00"},
                    {"max_attempts": 0}, {"max_attempts": 101},
                    {"start_at": "2099-09-02T09:00:00Z"}):
        invalid = await client.post(
            f"/api/assessment-core/assessments/{assessment['id']}/publish-and-assign",
            json={**payload, **changes})
        assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "validation_error"
    empty = await create(client, "Empty variant")
    await client.post(f"/api/assessment-core/assessments/{empty['id']}/variants", json={"name": "A"})
    empty_result = await client.post(
        f"/api/assessment-core/assessments/{empty['id']}/publish-and-assign", json=payload)
    assert empty_result.status_code == 422
    assert empty_result.json()["error"]["details"][0]["code"] == "empty_variant"

    async with factory() as session:
        archived_group = ClassGroup(name="Archived group", created_by=uuid4(),
            archived_at=await session.scalar(text("SELECT clock_timestamp()")))
        empty_group = ClassGroup(name="Empty group", created_by=uuid4())
        session.add_all([archived_group, empty_group]); await session.commit()
        archived_group_id, empty_group_id = archived_group.id, empty_group.id
    for group_id, expected in ((archived_group_id, "inactive_or_missing_group"),
                               (empty_group_id, "no_active_students")):
        actor, _, ready_id, _, _, _ = await publication_fixture(engine, factory)
        result = await client.post(f"/api/assessment-core/assessments/{ready_id}/publish-and-assign",
            json={**payload, "class_group_id": str(group_id)})
        assert result.status_code == 422 and result.json()["error"]["details"][0]["code"] == expected
    actor, _, ready_id, _, ready_group, _ = await publication_fixture(engine, factory)
    passed = await client.post(f"/api/assessment-core/assessments/{ready_id}/publish-and-assign",
        json={**payload, "class_group_id": str(ready_group), "due_at": "2026-08-01T10:00:00Z"})
    assert passed.status_code == 422 and passed.json()["error"]["details"][0]["code"] == "due_at_not_future"


@pytest.mark.parametrize("archive_task", [False, True], ids=["version_archived_after_add", "task_archived_after_add"])
async def test_publication_rejects_content_archived_after_composition(database, archive_task):
    engine, factory = database
    actor, content, assessment_id, _, group_id, _ = await publication_fixture(engine, factory)
    async with engine.begin() as connection:
        if archive_task:
            await connection.execute(text("UPDATE tasks SET archived_at=clock_timestamp() WHERE id=:id"), {"id": content["task"]})
        else:
            await connection.execute(text("UPDATE task_versions SET status='archived' WHERE id=:id"), {"id": content["version"]})
    with pytest.raises(AssessmentError) as error:
        await AssessmentService(SQLAlchemyAssessmentUnitOfWork(factory)).publish_and_assign(
            publish_command(assessment_id, group_id), actor)
    assert error.value.code == "invalid_task_version"
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status='draft' FROM assessments WHERE id=:id"), {"id": assessment_id})
        assert await connection.scalar(text("SELECT count(*) FROM assignments WHERE assessment_id=:id"), {"id": assessment_id}) == 0


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


class PausingAssessmentUnitOfWork(SQLAlchemyAssessmentUnitOfWork):
    """Pause immediately after a production Assessment FOR UPDATE succeeds."""
    def __init__(self, factory, acquired=None, release=None, started=None):
        super().__init__(factory); self.acquired = acquired; self.release = release; self.started = started

    async def __aenter__(self):
        await super().__aenter__()
        delegate = self.repository
        owner = self
        class RepositoryProxy:
            def __getattr__(self, name): return getattr(delegate, name)
            async def lock_scoped(self, assessment_id, scope):
                owner.pid = await owner.session.scalar(text("SELECT pg_backend_pid()"))
                if owner.started is not None: owner.started.set()
                result = await delegate.lock_scoped(assessment_id, scope)
                if owner.acquired is not None: owner.acquired.set()
                if owner.release is not None: await owner.release.wait()
                return result
        self.repository = RepositoryProxy()
        return self


class PausingPublicationUnitOfWork(SQLAlchemyAssessmentUnitOfWork):
    """Pause around the production batch Content Bank lock/revalidation call."""
    def __init__(self, factory, locked=None, release=None, started=None):
        super().__init__(factory); self.locked = locked; self.release = release; self.started = started

    async def __aenter__(self):
        await super().__aenter__()
        delegate = SQLAlchemyContentBankReadPort(self.session)
        owner = self
        class PortProxy:
            async def lock_publication_usage(self, version_ids):
                owner.pid = await owner.session.scalar(text("SELECT pg_backend_pid()"))
                if owner.started is not None: owner.started.set()
                result = await delegate.lock_publication_usage(version_ids)
                if owner.locked is not None: owner.locked.set()
                if owner.release is not None: await owner.release.wait()
                return result
            async def lock_new_usage(self, version_id):
                return await delegate.lock_new_usage(version_id)
        self.content_bank = PortProxy()
        return self


async def publication_fixture(engine, factory, *, students=2):
    actor = ActorContext(TEACHER_ID); content = await content_version(engine)
    async with factory() as session:
        assessment = Assessment(title="Controlled publication", created_by=actor.actor_id)
        session.add(assessment); await session.flush()
        variant = AssessmentVariant(assessment_id=assessment.id, name="A", position=1)
        session.add(variant); await session.flush()
        from app.infrastructure.assessment_models import AssessmentItem
        session.add(AssessmentItem(variant_id=variant.id, task_version_id=content["version"],
                                   position=1, points=Decimal("1.00")))
        group = ClassGroup(name=f"Group {uuid4()}", created_by=actor.actor_id)
        session.add(group); await session.flush()
        pupils = [Student(class_group_id=group.id, display_name=f"Student {index}")
                  for index in range(students)]
        session.add_all(pupils); await session.commit()
        return actor, content, assessment.id, variant.id, group.id, tuple(student.id for student in pupils)


def publish_command(assessment_id, group_id):
    return PublishAssessmentCommand(assessment_id, group_id,
        datetime.fromisoformat("2026-08-01T09:00:00+00:00"),
        datetime.fromisoformat("2099-09-01T10:00:00+00:00"), 1)


async def test_concurrent_publish_serializes_single_assignment(database):
    engine, factory = database
    actor, _, assessment_id, _, group_id, students = await publication_fixture(engine, factory)
    winner_locked = asyncio.Event(); release_winner = asyncio.Event()
    winner_uow = PausingAssessmentUnitOfWork(factory, acquired=winner_locked, release=release_winner)
    winner = asyncio.create_task(AssessmentService(winner_uow).publish_and_assign(
        publish_command(assessment_id, group_id), actor))
    await asyncio.wait_for(winner_locked.wait(), timeout=5)
    loser_started = asyncio.Event()
    loser_uow = PausingAssessmentUnitOfWork(factory, started=loser_started)
    loser = asyncio.create_task(AssessmentService(loser_uow).publish_and_assign(
        publish_command(assessment_id, group_id), actor))
    await asyncio.wait_for(loser_started.wait(), timeout=5)
    await assert_postgres_lock_wait(engine, loser_uow.pid)
    release_winner.set()
    result = await asyncio.wait_for(winner, timeout=5)
    with pytest.raises(AssessmentError) as error:
        await asyncio.wait_for(loser, timeout=5)
    assert error.value.code == "assessment_immutable"
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status='published' FROM assessments WHERE id=:id"), {"id": assessment_id})
        assignment_ids = (await connection.execute(text(
            "SELECT id FROM assignments WHERE assessment_id=:id"), {"id": assessment_id})).scalars().all()
        assert assignment_ids == [result.assignment.id]
        assert await connection.scalar(text(
            "SELECT count(*) FROM assignment_participants WHERE assignment_id=:id"), {"id": result.assignment.id}) == len(students)
        assert await connection.scalar(text(
            "SELECT count(*) FROM assignment_participants p LEFT JOIN assignments a ON a.id=p.assignment_id "
            "WHERE a.assessment_id=:id AND a.id IS NULL"), {"id": assessment_id}) == 0
        for event in ("assessment_published", "assignment_created"):
            assert await connection.scalar(text(
                "SELECT count(*) FROM assessment_audit_log WHERE event_type=:event AND "
                "(aggregate_id=:assessment OR aggregate_id=:assignment)"),
                {"event": event, "assessment": assessment_id, "assignment": result.assignment.id}) == 1


async def test_composition_wins_publish_freezes_revised_state(database):
    engine, factory = database
    actor, _, assessment_id, variant_id, group_id, _ = await publication_fixture(engine, factory)
    async with factory() as session:
        assessment = await session.get(Assessment, assessment_id)
        item_id = await session.scalar(text("SELECT id FROM assessment_items WHERE variant_id=:id"), {"id": variant_id})
        token = assessment.updated_at
    mutation_locked = asyncio.Event(); release_mutation = asyncio.Event()
    mutation_uow = PausingAssessmentUnitOfWork(factory, acquired=mutation_locked, release=release_mutation)
    mutation = asyncio.create_task(AssessmentService(mutation_uow).change_item_points(
        ChangeAssessmentItemPointsCommand(assessment_id, variant_id, item_id, Decimal("7.00"), token), actor))
    await asyncio.wait_for(mutation_locked.wait(), timeout=5)
    publish_started = asyncio.Event(); publish_uow = PausingAssessmentUnitOfWork(factory, started=publish_started)
    publish = asyncio.create_task(AssessmentService(publish_uow).publish_and_assign(
        publish_command(assessment_id, group_id), actor))
    await asyncio.wait_for(publish_started.wait(), timeout=5)
    await assert_postgres_lock_wait(engine, publish_uow.pid)
    release_mutation.set()
    await asyncio.wait_for(mutation, timeout=5)
    await asyncio.wait_for(publish, timeout=5)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT points=7 FROM assessment_items WHERE id=:id"), {"id": item_id})
        events = (await connection.execute(text(
            "SELECT event_type FROM assessment_audit_log WHERE aggregate_id=:id"), {"id": assessment_id})).scalars().all()
        assert events.count("item_points_changed") == 1 and events.count("assessment_published") == 1


async def test_publish_wins_composition_becomes_immutable(database):
    engine, factory = database
    actor, _, assessment_id, variant_id, group_id, _ = await publication_fixture(engine, factory)
    async with factory() as session:
        assessment = await session.get(Assessment, assessment_id)
        item_id = await session.scalar(text("SELECT id FROM assessment_items WHERE variant_id=:id"), {"id": variant_id})
        token = assessment.updated_at
    publish_locked = asyncio.Event(); release_publish = asyncio.Event()
    publish_uow = PausingAssessmentUnitOfWork(factory, acquired=publish_locked, release=release_publish)
    publish = asyncio.create_task(AssessmentService(publish_uow).publish_and_assign(
        publish_command(assessment_id, group_id), actor))
    await asyncio.wait_for(publish_locked.wait(), timeout=5)
    mutation_started = asyncio.Event(); mutation_uow = PausingAssessmentUnitOfWork(factory, started=mutation_started)
    mutation = asyncio.create_task(AssessmentService(mutation_uow).change_item_points(
        ChangeAssessmentItemPointsCommand(assessment_id, variant_id, item_id, Decimal("9.00"), token), actor))
    await asyncio.wait_for(mutation_started.wait(), timeout=5)
    await assert_postgres_lock_wait(engine, mutation_uow.pid)
    release_publish.set(); await asyncio.wait_for(publish, timeout=5)
    with pytest.raises(AssessmentError) as error:
        await asyncio.wait_for(mutation, timeout=5)
    assert error.value.code == "assessment_immutable"
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT points=1 FROM assessment_items WHERE id=:id"), {"id": item_id})
        assert await connection.scalar(text(
            "SELECT count(*) FROM assessment_audit_log WHERE aggregate_id=:id AND event_type='item_points_changed'"), {"id": assessment_id}) == 0


async def test_archive_wins_publication_revalidates_without_partial_state(database):
    engine, factory = database
    actor, content, assessment_id, _, group_id, _ = await publication_fixture(engine, factory)
    archive_locked = asyncio.Event(); release_archive = asyncio.Event()
    archive_uow = PausingArchiveUnitOfWork(factory, archive_locked, release_archive)
    archive = asyncio.create_task(ArchiveTaskService(archive_uow).archive(content["task"], actor))
    await asyncio.wait_for(archive_locked.wait(), timeout=5)
    publication_started = asyncio.Event()
    publication_uow = PausingPublicationUnitOfWork(factory, started=publication_started)
    publication = asyncio.create_task(AssessmentService(publication_uow).publish_and_assign(
        publish_command(assessment_id, group_id), actor))
    await asyncio.wait_for(publication_started.wait(), timeout=5)
    await assert_postgres_lock_wait(engine, publication_uow.pid)
    release_archive.set(); await asyncio.wait_for(archive, timeout=5)
    with pytest.raises(AssessmentError) as error:
        await asyncio.wait_for(publication, timeout=5)
    assert error.value.code == "invalid_task_version"
    async with engine.connect() as connection:
        state = (await connection.execute(text(
            "SELECT status,published_at,published_by FROM assessments WHERE id=:id"), {"id": assessment_id})).one()
        assert tuple(state) == ("draft", None, None)
        assert await connection.scalar(text("SELECT count(*) FROM assignments WHERE assessment_id=:id"), {"id": assessment_id}) == 0
        assert await connection.scalar(text(
            "SELECT count(*) FROM assessment_audit_log WHERE event_type IN ('assessment_published','assignment_created') "
            "AND (aggregate_id=:id OR details->>'assessment_id'=:sid)"), {"id": assessment_id, "sid": str(assessment_id)}) == 0


async def test_publication_wins_archive_waits_and_history_survives(database):
    engine, factory = database
    actor, content, assessment_id, _, group_id, students = await publication_fixture(engine, factory)
    publication_locked = asyncio.Event(); release_publication = asyncio.Event()
    publication_uow = PausingPublicationUnitOfWork(factory, locked=publication_locked, release=release_publication)
    publication = asyncio.create_task(AssessmentService(publication_uow).publish_and_assign(
        publish_command(assessment_id, group_id), actor))
    await asyncio.wait_for(publication_locked.wait(), timeout=5)
    archive_started = asyncio.Event(); archive_after_locks = asyncio.Event(); release_archive = asyncio.Event()
    archive_uow = PausingArchiveUnitOfWork(factory, archive_after_locks, release_archive, started=archive_started)
    archive = asyncio.create_task(ArchiveTaskService(archive_uow).archive(content["task"], actor))
    await asyncio.wait_for(archive_started.wait(), timeout=5)
    await assert_postgres_lock_wait(engine, archive_uow.pid)
    release_publication.set(); published = await asyncio.wait_for(publication, timeout=5)
    await asyncio.wait_for(archive_after_locks.wait(), timeout=5)
    release_archive.set(); await asyncio.wait_for(archive, timeout=5)
    summary = await AssessmentService(SQLAlchemyAssessmentUnitOfWork(factory)).get_assignment(
        published.assignment.id, actor)
    assert summary.participant_count == len(students)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status='published' FROM assessments WHERE id=:id"), {"id": assessment_id})
        assert await connection.scalar(text("SELECT archived_at IS NOT NULL FROM tasks WHERE id=:id"), {"id": content["task"]})
        assert await connection.scalar(text("SELECT status='archived' FROM task_versions WHERE id=:id"), {"id": content["version"]})
        assert await connection.scalar(text(
            "SELECT count(*) FROM assessment_items WHERE task_version_id=:id"), {"id": content["version"]}) == 1


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
    actor = ActorContext(TEACHER_ID)
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
    actor = ActorContext(TEACHER_ID)
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
    actor = ActorContext(TEACHER_ID); content = await content_version(engine)
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
    actor = ActorContext(TEACHER_ID); content = await content_version(engine)
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
