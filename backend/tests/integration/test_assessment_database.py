"""PostgreSQL proofs for the Phase 3.1 Assessment Core constraints."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

URL = os.environ.get("TEST_DATABASE_URL", "")
if not URL:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Assessment DB tests require a database ending in _test")

pytestmark = pytest.mark.asyncio


async def rejected(connection, sql, values):
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(text(sql), values)


@pytest_asyncio.fixture
async def engine():
    """Keep each asyncpg pool within one test's pytest-asyncio event loop."""
    value = create_async_engine(URL)
    try:
        yield value
    finally:
        await value.dispose()


@pytest_asyncio.fixture
async def db(engine):
    ids = {key: uuid4() for key in ("actor", "group", "student", "assessment", "variant", "variant2", "item", "task", "version", "version2", "assignment", "participant", "submission")}
    async with engine.begin() as connection:
        current = await connection.scalar(text("SELECT current_database()"))
        assert current.endswith("_test")
        await connection.execute(text("TRUNCATE assessment_audit_log, assessment_idempotency_keys, student_answers, student_submissions, assignment_participants, assignments, assessment_items, assessment_variants, assessments, students, class_groups, audit_log, task_skill_links, task_versions, tasks, skills, subtopics, topics, grades, subjects CASCADE"))
        catalog = {key: uuid4() for key in ("subject", "grade", "topic")}
        for sql in ("INSERT INTO subjects(id,code,name,normalized_name) VALUES (:subject,'assessment-test','Assessment test','assessment test')", "INSERT INTO grades(id,number,name,normalized_name) VALUES (:grade,7,'7','7')", "INSERT INTO topics(id,subject_id,grade_id,code,name,normalized_name) VALUES (:topic,:subject,:grade,'assessment-test','Assessment test','assessment test')"):
            await connection.execute(text(sql), catalog)
        await connection.execute(text("INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject,:grade,:topic,:actor)"), {**ids, **catalog})
        await connection.execute(text("INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:version,:task,1,'Statement','problem','short_text',50,'approved',:actor)"), ids)
        await connection.execute(text("INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:version2,:task,2,'Second statement','problem','short_text',50,'draft',:actor)"), ids)
        for sql in ("INSERT INTO class_groups(id,name,external_ref,created_by) VALUES (:group,'Pilot','group-1',:actor)", "INSERT INTO students(id,class_group_id,display_name,external_ref) VALUES (:student,:group,'Student','student-1')", "INSERT INTO assessments(id,title,created_by) VALUES (:assessment,'Assessment',:actor)", "INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES (:variant,:assessment,'A',1),(:variant2,:assessment,'B',2)", "INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item,:variant,:version,1,1.00)"):
            await connection.execute(text(sql), ids)
        now = datetime.now(timezone.utc)
        await connection.execute(text("INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by) VALUES (:assignment,:assessment,:group,:start,:due,:actor)"), {**ids, "start": now, "due": now + timedelta(hours=1)})
        await connection.execute(text("INSERT INTO assignment_participants(id,assignment_id,student_id) VALUES (:participant,:assignment,:student)"), ids)
        await connection.execute(text("INSERT INTO student_submissions(id,assignment_participant_id,attempt_no) VALUES (:submission,:participant,1)"), ids)
    try:
        yield ids
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE assessment_audit_log, assessment_idempotency_keys, student_answers, student_submissions, assignment_participants, assignments, assessment_items, assessment_variants, assessments, students, class_groups CASCADE"))


async def test_basic_rows_status_and_assessment_has_many_assignments(engine, db):
    async with engine.begin() as c:
        assert await c.scalar(text("SELECT count(*) FROM students WHERE class_group_id=:group"), db) == 1
        assert await c.scalar(text("SELECT status::text FROM assessments WHERE id=:assessment"), db) == "draft"
        await c.execute(text("INSERT INTO assignments(assessment_id,class_group_id,start_at,due_at,created_by) VALUES (:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 hour',:actor)"), db)
        assert await c.scalar(text("SELECT count(*) FROM assignments WHERE assessment_id=:assessment"), db) == 2


async def test_variant_and_item_uniqueness_and_ranges(engine, db):
    async with engine.begin() as c:
        await rejected(c, "INSERT INTO assessment_variants(assessment_id,name,position) VALUES (:assessment,'C',1)", db)
        await rejected(c, "INSERT INTO assessment_variants(assessment_id,name,position) VALUES (:assessment,'A',3)", db)
        await rejected(c, "INSERT INTO assessment_items(variant_id,task_version_id,position,points) VALUES (:variant2,:version,0,1)", db)
        await rejected(c, "INSERT INTO assessment_items(variant_id,task_version_id,position,points) VALUES (:variant,:version2,1,1)", db)
        await rejected(c, "INSERT INTO assessment_items(variant_id,task_version_id,position,points) VALUES (:variant,:version,2,1)", db)
        await rejected(c, "INSERT INTO assessment_items(variant_id,task_version_id,position,points) VALUES (:variant2,:version,1,0)", db)
        await rejected(c, "INSERT INTO assessment_items(variant_id,task_version_id,position,points) VALUES (:variant2,:missing,1,1)", {**db, "missing": uuid4()})


async def test_assignment_participant_and_variant_pair_constraints(engine, db):
    now = datetime.now(timezone.utc)
    async with engine.begin() as c:
        await rejected(c, "INSERT INTO assignments(assessment_id,class_group_id,start_at,due_at,max_attempts,created_by) VALUES (:assessment,:group,:now,:now,1,:actor)", {**db, "now": now})
        await rejected(c, "INSERT INTO assignments(assessment_id,class_group_id,start_at,due_at,max_attempts,created_by) VALUES (:assessment,:group,:now,:due,0,:actor)", {**db, "now": now, "due": now + timedelta(hours=1)})
        await rejected(c, "UPDATE assignments SET status='closed' WHERE id=:assignment", db)
        await rejected(c, "INSERT INTO assignment_participants(assignment_id,student_id) VALUES (:assignment,:student)", db)
        await rejected(c, "UPDATE assignment_participants SET assigned_variant_id=:variant WHERE id=:participant", db)


async def test_submission_partial_draft_unique_state_and_answers(engine, db):
    async with engine.begin() as c:
        await rejected(c, "INSERT INTO student_submissions(assignment_participant_id,attempt_no) VALUES (:participant,0)", db)
        await rejected(c, "INSERT INTO student_submissions(assignment_participant_id,attempt_no) VALUES (:participant,1)", db)
        await rejected(c, "INSERT INTO student_submissions(assignment_participant_id,attempt_no) VALUES (:participant,2)", db)
        await rejected(c, "UPDATE student_submissions SET status='submitted' WHERE id=:submission", db)
        await c.execute(text("INSERT INTO student_answers(submission_id,assessment_item_id,raw_answer,normalized_answer) VALUES (:submission,:item,'{}','{}')"), db)
        await rejected(c, "INSERT INTO student_answers(submission_id,assessment_item_id,raw_answer,normalized_answer) VALUES (:submission,:item,'{}','{}')", db)


async def test_idempotency_namespace_and_historical_restrict(engine, db):
    values = {**db, "key": "same-key", "hash": "a" * 64}
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO assessment_idempotency_keys(assignment_participant_id,key,operation,request_hash,submission_id,http_status) VALUES (:participant,:key,'start',:hash,:submission,201)"), values)
        await rejected(c, "INSERT INTO assessment_idempotency_keys(assignment_participant_id,key,operation,request_hash,submission_id,http_status) VALUES (:participant,:key,'submit',:hash,:submission,200)", values)
        await rejected(c, "DELETE FROM task_versions WHERE id=:version", db)


async def test_critical_foreign_keys_are_restrict(engine, db):
    async with engine.begin() as c:
        await rejected(c, "DELETE FROM assessments WHERE id=:assessment", db)
        await rejected(c, "DELETE FROM students WHERE id=:student", db)
