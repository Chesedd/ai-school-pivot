"""Real PostgreSQL vertical and controlled-lock proofs for student attempts."""
import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("TEST_DATABASE_URL", "")
if not URL:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Student assessment tests require a database ending in _test")

from app.application.student_assessments import PilotStudentContext
from app.infrastructure.assessment_models import (Assignment, AssignmentParticipant,
    AssessmentAuditLog, AssessmentIdempotencyKey, StudentAnswer, StudentSubmission)
from app.infrastructure.student_assessment_repository import StudentAssessmentService
from app.main import app
import app.presentation.student_assessment_routes as student_routes

pytestmark = pytest.mark.asyncio
RACE = pytest.mark.student_race


async def yield_control():
    future = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(future.set_result, None)
    await future


@pytest_asyncio.fixture
async def database(monkeypatch):
    engine = create_async_engine(URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(student_routes, "async_session_factory", factory)
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE assessment_audit_log, assessment_idempotency_keys, student_answers, "
            "student_submissions, assignment_participants, assignments, assessment_items, assessment_variants, "
            "assessments, students, class_groups, task_versions, tasks, topics, grades, subjects CASCADE"))
    try:
        yield engine, factory
    finally:
        app.dependency_overrides.pop(student_routes.student_context, None)
        await engine.dispose()


@pytest_asyncio.fixture
async def client(database):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://test") as value:
        yield value


async def scenario(database, *, formats=("short_text", "short_text"), max_attempts=2,
                   start=None, due=None, assignment_id=None, student_id=None):
    engine, _ = database
    ids = {name: uuid4() for name in ("actor", "group", "foreign_group", "student", "foreign_student",
        "assessment", "foreign_assessment", "assignment", "foreign_assignment", "participant",
        "foreign_participant", "variant_a", "variant_b", "foreign_variant")}
    if assignment_id: ids["assignment"] = assignment_id
    if student_id: ids["student"] = student_id
    now = datetime.now(timezone.utc)
    start = start or now - timedelta(minutes=5); due = due or now + timedelta(hours=1)
    async with engine.begin() as c:
        async def execute_many(sql, values):
            for statement in sql.split(";"):
                if statement.strip():
                    await c.execute(text(statement), values)
        await c.execute(text("INSERT INTO class_groups(id,name,created_by) VALUES "
            "(:group,'Own group',:actor),(:foreign_group,'Foreign group',:actor)"), ids)
        await c.execute(text("INSERT INTO students(id,class_group_id,display_name) VALUES "
            "(:student,:group,'Current'),(:foreign_student,:foreign_group,'Foreign')"), ids)
        await c.execute(text("INSERT INTO assessments(id,title,status,created_by,published_at,published_by) VALUES "
            "(:assessment,'Student assessment','published',:actor,clock_timestamp(),:actor),"
            "(:foreign_assessment,'Foreign assessment','published',:actor,clock_timestamp(),:actor)"), ids)
        await c.execute(text("INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES "
            "(:variant_b,:assessment,'B',2),(:variant_a,:assessment,'A',1),"
            "(:foreign_variant,:foreign_assessment,'F',1)"), ids)
        base = {name: uuid4() for name in ("subject", "grade", "topic")}
        base.update(suffix=str(uuid4()))
        await execute_many("INSERT INTO subjects(id,code,name) VALUES (:subject,:suffix,'S');"
            "INSERT INTO grades(id,number,name) VALUES (:grade,10,'10');"
            "INSERT INTO topics(id,subject_id,grade_id,code,name) VALUES (:topic,:subject,:grade,:suffix,'T')", base)
        for index, (variant, answer_format) in enumerate(zip((ids["variant_a"], ids["variant_b"]), formats)):
            catalog = {name: uuid4() for name in ("task", "version", "item")}
            catalog.update(base, actor=ids["actor"], variant=variant, fmt=answer_format)
            await execute_many("INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject,:grade,:topic,:actor);"
                "INSERT INTO task_versions(id,task_id,version_no,title,statement,task_type,answer_format,difficulty,status,created_by) "
                "VALUES (:version,:task,1,'Historical title','Historical statement','problem',CAST(:fmt AS answer_format),50,'approved',:actor);"
                "INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item,:variant,:version,1,2.00)", catalog)
            ids[f"item_{index}"] = catalog["item"]; ids[f"version_{index}"] = catalog["version"]; ids[f"task_{index}"] = catalog["task"]
        catalog = {name: uuid4() for name in ("task", "version", "item")}
        catalog.update(base, actor=ids["actor"], variant=ids["foreign_variant"])
        await execute_many("INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject,:grade,:topic,:actor);"
            "INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) "
            "VALUES (:version,:task,1,'Other','problem','short_text',50,'approved',:actor);"
            "INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item,:variant,:version,1,1)", catalog)
        ids["foreign_item"] = catalog["item"]
        await c.execute(text("INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,max_attempts,created_by) VALUES "
            "(:assignment,:assessment,:group,:start,:due,:max_attempts,:actor),"
            "(:foreign_assignment,:foreign_assessment,:foreign_group,:start,:due,1,:actor)"),
            {**ids, "start": start, "due": due, "max_attempts": max_attempts})
        await c.execute(text("INSERT INTO assignment_participants(id,assignment_id,student_id) VALUES "
            "(:participant,:assignment,:student),(:foreign_participant,:foreign_assignment,:foreign_student)"), ids)
    app.dependency_overrides[student_routes.student_context] = lambda: PilotStudentContext(ids["student"])
    return ids


async def start(client, ids, key="start-1"):
    return await client.post(f"/api/assessment-core/student/assignments/{ids['assignment']}/attempts/start",
                             json={}, headers={"Idempotency-Key": key})


async def counts(factory, ids):
    async with factory() as s:
        return {
            "submissions": await s.scalar(select(text("count(*)")).select_from(StudentSubmission).where(StudentSubmission.assignment_participant_id == ids["participant"])),
            "keys": await s.scalar(select(text("count(*)")).select_from(AssessmentIdempotencyKey).where(AssessmentIdempotencyKey.assignment_participant_id == ids["participant"])),
            **{event: await s.scalar(select(text("count(*)")).select_from(AssessmentAuditLog).where(
                AssessmentAuditLog.event_type == event)) for event in ("variant_assigned", "submission_started", "answer_saved", "answer_deleted", "submission_submitted")}}


async def assert_postgres_lock_wait(engine, pid):
    async def poll():
        while True:
            async with engine.connect() as c:
                waiting = await c.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid})
            if waiting:
                return
            await yield_control()
    await asyncio.wait_for(poll(), 5)


def install_lock_gate(monkeypatch, method_name):
    original = getattr(StudentAssessmentService, method_name)
    locked = asyncio.Event(); release = asyncio.Event(); pids = []; calls = 0
    async def wrapper(self, session, *args, **kwargs):
        nonlocal calls
        mine = calls; calls += 1
        pids.append(await session.scalar(text("SELECT pg_backend_pid()")))
        result = await original(self, session, *args, **kwargs)
        if mine == 0:
            locked.set(); await release.wait()
        return result
    monkeypatch.setattr(StudentAssessmentService, method_name, wrapper)
    return locked, release, pids


async def test_student_reads_snapshot_ownership_and_spoofing(client, database):
    engine, factory = database; ids = await scenario(database)
    older_assignment, older_participant = uuid4(), uuid4()
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,max_attempts,created_at,created_by) "
            "VALUES (:id,:assessment,:group,clock_timestamp()-interval '1 hour',clock_timestamp()+interval '1 hour',1,clock_timestamp()-interval '1 hour',:actor)"),
            {**ids, "id": older_assignment})
        await c.execute(text("INSERT INTO assignment_participants(id,assignment_id,student_id) VALUES (:id,:assignment,:student)"),
            {"id": older_participant, "assignment": older_assignment, "student": ids["student"]})
    own = await client.get("/api/assessment-core/student/assignments?offset=0&limit=1",
                           headers={"X-Student-Id": str(ids["foreign_student"])})
    assert own.status_code == 200 and own.json()["total"] == 2
    assert own.json()["items"][0]["assignment_id"] == str(ids["assignment"])
    second_page = await client.get("/api/assessment-core/student/assignments?offset=1&limit=1")
    assert second_page.json()["items"][0]["assignment_id"] == str(older_assignment)
    assert (await client.get(f"/api/assessment-core/student/assignments/{ids['foreign_assignment']}")).json()["error"]["code"] == "assignment_not_found"
    detail = await client.get(f"/api/assessment-core/student/assignments/{ids['assignment']}?student_id={ids['foreign_student']}")
    assert detail.status_code == 200 and detail.json()["assigned_variant_id"] is None
    body_spoof = await client.post(f"/api/assessment-core/student/assignments/{ids['assignment']}/attempts/start",
        json={"student_id": str(ids["foreign_student"])}, headers={"Idempotency-Key": "spoof"})
    assert body_spoof.status_code == 422
    async with factory() as s:
        assert await s.scalar(select(text("count(*)")).select_from(StudentSubmission)) == 0
        participant = await s.get(AssignmentParticipant, ids["participant"])
        assert participant.assigned_variant_id is None
        student = await s.execute(text("UPDATE students SET archived_at=clock_timestamp(),class_group_id=:foreign_group WHERE id=:student"), ids)
        await s.commit()
    assert (await client.get(f"/api/assessment-core/student/assignments/{ids['assignment']}")).status_code == 200


async def test_submitted_attempt_history_is_owned_sorted_bounded_and_answer_free(client, database):
    engine, _ = database
    ids = await scenario(database, max_attempts=3)
    async with engine.begin() as connection:
        timestamps = (await connection.execute(text(
            "SELECT clock_timestamp()-interval '3 hours' AS first, "
            "clock_timestamp()-interval '2 hours' AS draft, clock_timestamp()-interval '1 hour' AS third"))).mappings().one()
        submission_ids = {"first": uuid4(), "draft": uuid4(), "third": uuid4(), "foreign": uuid4()}
        await connection.execute(text("INSERT INTO student_submissions"
            "(id,assignment_participant_id,attempt_no,status,started_at,submitted_at) VALUES "
            "(:first,:participant,1,'submitted',:first_at,:first_at),"
            "(:draft,:participant,2,'draft',:draft_at,NULL),"
            "(:third,:participant,3,'submitted',:third_at,:third_at),"
            "(:foreign,:foreign_participant,1,'submitted',:first_at,:first_at)"),
            {**submission_ids, **ids, "first_at": timestamps["first"], "draft_at": timestamps["draft"],
             "third_at": timestamps["third"]})
        await connection.execute(text("INSERT INTO student_answers"
            "(submission_id,assessment_item_id,raw_answer,normalized_answer) "
            "VALUES (:submission,:item,CAST(:raw AS jsonb),CAST(:normalized AS jsonb))"),
            {"submission": submission_ids["first"], "item": ids["item_0"],
             "raw": '"secret answer"', "normalized": '{"text":"secret answer"}'})
    detail = await client.get(f"/api/assessment-core/student/assignments/{ids['assignment']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["submitted_attempt_count"] == len(body["submitted_attempts"]) == 2
    assert [row["id"] for row in body["submitted_attempts"]] == [
        str(submission_ids["first"]), str(submission_ids["third"])]
    assert [row["attempt_no"] for row in body["submitted_attempts"]] == [1, 3]
    assert [row["submitted_at"] for row in body["submitted_attempts"]] == [
        timestamps["first"].isoformat().replace("+00:00", "Z"),
        timestamps["third"].isoformat().replace("+00:00", "Z")]
    assert body["current_draft_attempt_id"] == str(submission_ids["draft"])
    serialized = str(body["submitted_attempts"])
    assert str(submission_ids["draft"]) not in serialized
    assert str(submission_ids["foreign"]) not in serialized
    assert "answers" not in serialized and "secret answer" not in serialized


async def test_start_new_replay_resume_db_contract(client, database):
    _, factory = database; ids = await scenario(database)
    created = await start(client, ids)
    assert created.status_code == 201 and created.headers["location"].endswith(created.json()["id"])
    assert created.json()["attempt_no"] == 1 and created.json()["resumed"] is False
    replay = await start(client, ids); assert replay.status_code == 201 and replay.json()["id"] == created.json()["id"]
    resumed = await start(client, ids, "start-2")
    assert resumed.status_code == 200 and resumed.json()["resumed"] is True and resumed.json()["id"] == created.json()["id"]
    values = await counts(factory, ids)
    assert values | {} == {**values, "submissions": 1, "keys": 2, "variant_assigned": 1, "submission_started": 1,
        "answer_saved": 0, "answer_deleted": 0, "submission_submitted": 0}
    async with factory() as s:
        p = await s.get(AssignmentParticipant, ids["participant"]); assert p.assigned_variant_id and p.variant_assigned_at
        keys = (await s.scalars(select(AssessmentIdempotencyKey).order_by(AssessmentIdempotencyKey.key))).all()
        assert [(x.key,x.operation,x.http_status,x.submission_id) for x in keys] == [
            ("start-1","start",201,UUID(created.json()["id"])),("start-2","start",200,UUID(created.json()["id"]))]
        assert all(len(x.request_hash) == 64 for x in keys)


async def test_vertical_variant_is_exact_and_fixed_for_attempt_two(client, database):
    _, factory = database
    aid=UUID("10000000-0000-4000-8000-000000000001"); sid=UUID("20000000-0000-4000-8000-000000000002")
    ids=await scenario(database,assignment_id=aid,student_id=sid,max_attempts=2)
    ordered=sorted([(1,ids["variant_a"]),(2,ids["variant_b"])])
    index=int.from_bytes(hashlib.sha256(aid.bytes+sid.bytes).digest()[:8],"big") % 2
    expected=ordered[index][1]
    first=await start(client,ids); assert UUID(first.json()["assigned_variant_id"]) == expected
    assert (await client.post(f"/api/assessment-core/student/attempts/{first.json()['id']}/submit",json={},headers={"Idempotency-Key":"submit-1"})).status_code == 200
    second=await start(client,ids,"start-next")
    assert second.status_code == 201 and second.json()["attempt_no"] == 2
    assert UUID(second.json()["assigned_variant_id"]) == expected


async def test_attempt_limit_rejects_without_success_key(client, database):
    _, factory = database; ids = await scenario(database, max_attempts=1)
    first = (await start(client, ids)).json()
    assert (await client.post(f"/api/assessment-core/student/attempts/{first['id']}/submit", json={},
        headers={"Idempotency-Key": "submit-limit"})).status_code == 200
    limited = await start(client, ids, "over-limit")
    assert limited.status_code == 409 and limited.json()["error"]["code"] == "attempt_limit_reached"
    async with factory() as s:
        assert await s.scalar(select(text("count(*)")).select_from(StudentSubmission)) == 1
        assert await s.scalar(select(text("count(*)")).select_from(AssessmentIdempotencyKey).where(
            AssessmentIdempotencyKey.key == "over-limit")) == 0


@pytest.mark.parametrize("header", [None,""," key","key ","a/b","ключ".encode("utf-8"),"x"*129])
async def test_start_rejects_noncanonical_key_without_state(client,database,header):
    _,factory=database; ids=await scenario(database)
    headers={} if header is None else {"Idempotency-Key":header}
    response=await client.post(f"/api/assessment-core/student/assignments/{ids['assignment']}/attempts/start",json={},headers=headers)
    assert response.status_code==400 and response.json()["error"]["code"]=="invalid_request"
    values=await counts(factory,ids)
    assert values["submissions"]==values["keys"]==values["variant_assigned"]==values["submission_started"]==0
    async with factory() as session:
        participant=await session.get(AssignmentParticipant,ids["participant"])
        assert participant.assigned_variant_id is None and participant.variant_assigned_at is None


@pytest.mark.parametrize("state,code", [("before","assignment_not_started"),("due","assignment_deadline_passed"),("closed","assignment_closed")])
async def test_failed_start_rolls_back_variant_audit_and_key(client,database,state,code):
    _,factory=database; now=datetime.now(timezone.utc)
    ids=await scenario(database,start=now+timedelta(hours=1) if state=="before" else now-timedelta(hours=2),
        due=now+timedelta(hours=2) if state=="before" else now-timedelta(hours=1) if state=="due" else now+timedelta(hours=1))
    if state=="closed":
        async with factory() as s:
            a=await s.get(Assignment,ids["assignment"]); a.status="closed"; a.closed_at=now; a.closed_by=ids["actor"]; await s.commit()
    response=await start(client,ids,"failed"); assert response.status_code==409 and response.json()["error"]["code"]==code
    values=await counts(factory,ids); assert values["submissions"]==values["keys"]==values["variant_assigned"]==values["submission_started"]==0


async def test_replay_precedes_close_and_cross_operation_conflicts(client,database):
    _,factory=database; ids=await scenario(database); created=await start(client,ids,"shared")
    async with factory() as s:
        a=await s.get(Assignment,ids["assignment"]); a.status="closed"; a.closed_at=datetime.now(timezone.utc); a.closed_by=ids["actor"]; await s.commit()
    assert (await start(client,ids,"shared")).status_code==201
    fresh=await start(client,ids,"fresh"); assert fresh.status_code==409 and fresh.json()["error"]["code"]=="assignment_closed"
    conflict=await client.post(f"/api/assessment-core/student/attempts/{created.json()['id']}/submit",json={},headers={"Idempotency-Key":"shared"})
    assert conflict.status_code==409 and conflict.json()["error"]["code"]=="idempotency_conflict"
    values=await counts(factory,ids); assert values["keys"]==1 and values["submission_submitted"]==0


NORMALIZED=[("single_choice","A",{"option_id":"A"}),
 ("multiple_choice",["b","a"],{"option_ids":["a","b"]}),
 ("short_text","  Café  ",{"text":"Café"}),
 ("number"," -0,00e2 ",{"decimal":"0"}),
 ("expression"," x + X ",{"expression":"x + X"}),
 ("long_text"," a\r\nb\r ",{"text":" a\nb\n "})]

@pytest.mark.parametrize("answer_format,raw,normalized",NORMALIZED)
async def test_answer_formats_persist_raw_and_normalized(client,database,answer_format,raw,normalized):
    _,factory=database; ids=await scenario(database,formats=(answer_format,answer_format)); attempt=(await start(client,ids)).json()
    item=attempt["items"][0]["id"]
    response=await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",json={"raw_answer":raw,"expected_updated_at":None})
    assert response.status_code==201 and response.headers["location"].endswith(item)
    assert response.json()["raw_answer"]==raw and response.json()["normalized_answer"]==normalized
    async with factory() as s:
        row=await s.scalar(select(StudentAnswer)); assert row.raw_answer==raw and row.normalized_answer==normalized
        audit=await s.scalar(select(AssessmentAuditLog).where(AssessmentAuditLog.event_type=="answer_saved"))
        assert "raw_answer" not in str(audit.details) and "normalized_answer" not in str(audit.details) and str(raw) not in str(audit.details)


async def test_answer_cas_null_delete_and_wrong_paths(client,database):
    _,factory=database; ids=await scenario(database); attempt=(await start(client,ids)).json(); item=attempt["items"][0]["id"]
    created=await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",json={"raw_answer":"one","expected_updated_at":None})
    stamp=created.json()["updated_at"]
    duplicate=await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",json={"raw_answer":"bad","expected_updated_at":None})
    assert duplicate.status_code==409
    updated=await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",json={"raw_answer":"winner","expected_updated_at":stamp})
    stale=await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",json={"raw_answer":"loser","expected_updated_at":stamp})
    assert updated.status_code==200 and stale.status_code==409
    for wrong in (ids["item_1"] if item!=str(ids["item_1"]) else ids["item_0"],ids["foreign_item"]):
        result=await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{wrong}",json={"raw_answer":"x","expected_updated_at":None})
        assert result.status_code==404 and result.json()["error"]["code"]=="item_not_found"
    foreign=await client.put(f"/api/assessment-core/student/attempts/{uuid4()}/answers/{item}",json={"raw_answer":"x","expected_updated_at":None})
    assert foreign.status_code==404 and foreign.json()["error"]["code"]=="submission_not_found"
    deleted=await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",json={"raw_answer":None,"expected_updated_at":None})
    assert deleted.status_code==204
    assert (await client.delete(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}")).status_code==204
    values=await counts(factory,ids); assert values["answer_saved"]==2 and values["answer_deleted"]==1


async def test_answer_create_with_non_null_expectation_and_foreign_submission_are_hidden(client,database):
    _, factory = database; ids = await scenario(database); attempt = (await start(client, ids)).json()
    item = attempt["items"][0]["id"]
    conflict = await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",
        json={"raw_answer": "x", "expected_updated_at": datetime.now(timezone.utc).isoformat()})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "concurrent_conflict"
    # Create a real foreign submission, then restore the current server identity.
    app.dependency_overrides[student_routes.student_context] = lambda: PilotStudentContext(ids["foreign_student"])
    foreign = (await client.post(f"/api/assessment-core/student/assignments/{ids['foreign_assignment']}/attempts/start",
        json={}, headers={"Idempotency-Key": "foreign-start"})).json()
    app.dependency_overrides[student_routes.student_context] = lambda: PilotStudentContext(ids["student"])
    hidden = await client.get(f"/api/assessment-core/student/attempts/{foreign['id']}")
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "submission_not_found"


async def test_submit_replay_freeze_and_historical_archive(client,database):
    _,factory=database; ids=await scenario(database); attempt=(await start(client,ids)).json()
    async with factory() as s:
        await s.execute(text("UPDATE tasks SET archived_at=clock_timestamp() WHERE id IN (:a,:b)"),{"a":ids["task_0"],"b":ids["task_1"]}); await s.commit()
    assert (await client.get(f"/api/assessment-core/student/attempts/{attempt['id']}")).status_code==200
    item=attempt["items"][0]["id"]
    saved=await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",
        json={"raw_answer":" historical ","expected_updated_at":None})
    assert saved.status_code==201 and saved.json()["normalized_answer"]=={"text":"historical"}
    submitted=await client.post(f"/api/assessment-core/student/attempts/{attempt['id']}/submit",json={},headers={"Idempotency-Key":"submit"})
    assert submitted.status_code==200 and submitted.json()["status"]=="submitted" and submitted.json()["submitted_at"]
    async with factory() as s:
        a=await s.get(Assignment,ids["assignment"]); a.status="closed"; a.closed_at=datetime.now(timezone.utc); a.closed_by=ids["actor"]; await s.commit()
    replay=await client.post(f"/api/assessment-core/student/attempts/{attempt['id']}/submit",json={},headers={"Idempotency-Key":"submit"})
    assert replay.status_code==200
    failed=await client.post(f"/api/assessment-core/student/attempts/{attempt['id']}/submit",json={},headers={"Idempotency-Key":"other"})
    assert failed.status_code==409 and failed.json()["error"]["code"]=="submission_already_submitted"
    assert (await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",json={"raw_answer":"x","expected_updated_at":None})).json()["error"]["code"]=="submission_already_submitted"
    assert (await client.delete(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}")).json()["error"]["code"]=="submission_already_submitted"
    values=await counts(factory,ids); assert values["submission_submitted"]==1 and values["keys"]==2


async def test_submit_allows_zero_answers(client, database):
    _, factory = database; ids = await scenario(database); attempt = (await start(client, ids)).json()
    response = await client.post(f"/api/assessment-core/student/attempts/{attempt['id']}/submit",
        json={}, headers={"Idempotency-Key": "zero-submit"})
    assert response.status_code == 200 and response.json()["answers"] == []
    values = await counts(factory, ids); assert values["submission_submitted"] == 1


async def _start_race(client,database,monkeypatch,same):
    engine,factory=database; ids=await scenario(database); locked,release,pids=install_lock_gate(monkeypatch,"_locked_start_context")
    first=asyncio.create_task(start(client,ids,"race-a")); await asyncio.wait_for(locked.wait(),5)
    second=asyncio.create_task(start(client,ids,"race-a" if same else "race-b"))
    while len(pids)<2: await yield_control()
    await assert_postgres_lock_wait(engine,pids[1]); release.set()
    a,b=await asyncio.gather(first,second); values=await counts(factory,ids)
    expected_statuses=[201,201] if same else [200,201]
    actual_statuses=sorted([a.status_code,b.status_code])
    assert actual_statuses==expected_statuses, (
        f"unexpected concurrent START responses: "
        f"first=({a.status_code}, {a.json()}), second=({b.status_code}, {b.json()})")
    assert a.json()["id"]==b.json()["id"]
    assert a.json()["assigned_variant_id"] is not None
    assert a.json()["assigned_variant_id"]==b.json()["assigned_variant_id"]
    assert a.json()["attempt_no"]==b.json()["attempt_no"]==1
    assert a.json()["items"] and b.json()["items"]
    assert values["submissions"]==values["variant_assigned"]==values["submission_started"]==1
    assert values["keys"]==(1 if same else 2)

@RACE
async def test_concurrent_start_same_key_replays_single_submission(client,database,monkeypatch): await _start_race(client,database,monkeypatch,True)

@RACE
async def test_concurrent_start_different_keys_resume_one_draft(client,database,monkeypatch): await _start_race(client,database,monkeypatch,False)


async def _prepared_answer(client,database):
    ids=await scenario(database); attempt=(await start(client,ids)).json(); item=attempt["items"][0]["id"]
    answer=(await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",json={"raw_answer":"base","expected_updated_at":None})).json()
    return ids,attempt,item,answer

@RACE
async def test_concurrent_stale_answer_saves_one_cas_winner(client,database,monkeypatch):
    engine,factory=database; ids,attempt,item,answer=await _prepared_answer(client,database)
    locked,release,pids=install_lock_gate(monkeypatch,"_locked_attempt_context")
    async def save(value): return await client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",json={"raw_answer":value,"expected_updated_at":answer["updated_at"]})
    first=asyncio.create_task(save("winner")); await asyncio.wait_for(locked.wait(),5); second=asyncio.create_task(save("loser"))
    while len(pids)<2: await yield_control()
    await assert_postgres_lock_wait(engine,pids[1]); release.set(); a,b=await asyncio.gather(first,second)
    assert sorted([a.status_code,b.status_code])==[200,409]
    values=await counts(factory,ids); assert values["answer_saved"]==2
    async with factory() as s:
        final = await s.scalar(select(StudentAnswer)); assert final.raw_answer == "winner" and final.normalized_answer == {"text":"winner"}


async def _save_submit_race(client,database,monkeypatch,submit_first):
    engine,factory=database; ids,attempt,item,answer=await _prepared_answer(client,database)
    locked,release,pids=install_lock_gate(monkeypatch,"_locked_attempt_context")
    save=lambda: client.put(f"/api/assessment-core/student/attempts/{attempt['id']}/answers/{item}",json={"raw_answer":"saved","expected_updated_at":answer["updated_at"]})
    submit=lambda: client.post(f"/api/assessment-core/student/attempts/{attempt['id']}/submit",json={},headers={"Idempotency-Key":"race-submit"})
    first=asyncio.create_task(submit() if submit_first else save()); await asyncio.wait_for(locked.wait(),5)
    second=asyncio.create_task(save() if submit_first else submit())
    while len(pids)<2: await yield_control()
    await assert_postgres_lock_wait(engine,pids[1]); release.set(); a,b=await asyncio.gather(first,second)
    if submit_first: assert a.status_code==200 and b.status_code==409
    else: assert a.status_code==200 and b.status_code==200 and b.json()["answers"][0]["raw_answer"]=="saved"
    values=await counts(factory,ids); assert values["submission_submitted"]==1

@RACE
async def test_save_wins_submit_includes_committed_answer(client,database,monkeypatch): await _save_submit_race(client,database,monkeypatch,False)

@RACE
async def test_submit_wins_save_rejects_loser(client,database,monkeypatch): await _save_submit_race(client,database,monkeypatch,True)


async def _submit_race(client,database,monkeypatch,same):
    engine,factory=database; ids=await scenario(database); attempt=(await start(client,ids)).json()
    locked,release,pids=install_lock_gate(monkeypatch,"_locked_attempt_context")
    def submit(key): return client.post(f"/api/assessment-core/student/attempts/{attempt['id']}/submit",json={},headers={"Idempotency-Key":key})
    first=asyncio.create_task(submit("submit-a")); await asyncio.wait_for(locked.wait(),5); second=asyncio.create_task(submit("submit-a" if same else "submit-b"))
    while len(pids)<2: await yield_control()
    await assert_postgres_lock_wait(engine,pids[1]); release.set(); a,b=await asyncio.gather(first,second)
    assert sorted([a.status_code,b.status_code])==([200,200] if same else [200,409])
    values=await counts(factory,ids); assert values["submission_submitted"]==1 and values["keys"]==(2 if same else 2)

@RACE
async def test_concurrent_submit_same_key_replays_one_transition(client,database,monkeypatch): await _submit_race(client,database,monkeypatch,True)

@RACE
async def test_concurrent_submit_different_keys_rejects_loser(client,database,monkeypatch): await _submit_race(client,database,monkeypatch,False)


@RACE
async def test_start_waiting_past_due_uses_fresh_database_clock(client,database):
    engine,factory=database; now=datetime.now(timezone.utc); due=now+timedelta(seconds=2)
    ids=await scenario(database,start=now-timedelta(minutes=1),due=due)
    blocker=await engine.connect(); tx=await blocker.begin()
    await blocker.execute(text("SELECT id FROM assignments WHERE id=:id FOR UPDATE"),{"id":ids["assignment"]})
    request=asyncio.create_task(start(client,ids,"deadline"))
    async with engine.connect() as observer:
        while True:
            rows=(await observer.execute(text("SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'"))).scalars().all()
            if rows: break
            await yield_control()
        async def wait_clock():
            while not await observer.scalar(text("SELECT clock_timestamp() >= :due"),{"due":due}): await yield_control()
        await asyncio.wait_for(wait_clock(),10)
    await tx.commit(); await blocker.close(); response=await request
    assert response.status_code==409 and response.json()["error"]["code"]=="assignment_deadline_passed"
    values=await counts(factory,ids); assert values["submissions"]==values["keys"]==values["variant_assigned"]==values["submission_started"]==0
