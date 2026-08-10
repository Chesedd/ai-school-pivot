"""Real PostgreSQL proofs for the complete Phase 4.2 intake boundary."""
import asyncio, copy, os
from uuid import uuid4
import pytest, pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.checking import ActiveRunConflict, IdempotencyConflict
from app.application.checking_intake import (CheckingIntakeRequest, CheckingIntakeService,
    InvalidCheckingInput, SubmissionNotSubmitted)
from app.infrastructure.checking_intake_repository import SQLAlchemyCheckingIntakeUnitOfWorkFactory
from app.infrastructure.checking_repository import CheckingRepository
from app.infrastructure.student_assessment_repository import AssessmentCheckingHandoffService
import app.application.checking_intake as intake_module
import app.application.student_assessments as phase3

URL=os.environ.get("TEST_DATABASE_URL","")
if URL and not URL.rsplit("/",1)[-1].split("?",1)[0].endswith("_test"):
    raise RuntimeError("intake tests require a database ending in _test")
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not URL,reason="TEST_DATABASE_URL is required")]

@pytest_asyncio.fixture
async def context():
    engine=create_async_engine(URL); factory=async_sessionmaker(engine,expire_on_commit=False)
    names=("actor","subject","grade","topic","subtopic","skill","task","version","new_version","group","student",
           "assessment","variant","item","item2","assignment","participant","submission")
    ids={x:uuid4() for x in names}
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE cost_events,model_runs,checker_events,check_findings,check_results,prompt_versions,check_runs,assessment_audit_log,assessment_idempotency_keys,student_answers,student_submissions,assignment_participants,assignments,assessment_items,assessment_variants,assessments,students,class_groups,audit_log,task_skill_links,task_versions,tasks,skills,subtopics,topics,grades,subjects CASCADE"))
        sqls=("INSERT INTO subjects(id,code,name) VALUES (:subject,'i','Intake')",
          "INSERT INTO grades(id,number,name) VALUES (:grade,7,'7')",
          "INSERT INTO topics(id,subject_id,grade_id,code,name) VALUES (:topic,:subject,:grade,'i','I')",
          "INSERT INTO subtopics(id,topic_id,code,name) VALUES (:subtopic,:topic,'i','I')",
          "INSERT INTO skills(id,subtopic_id,code,name) VALUES (:skill,:subtopic,'s','Skill')",
          "INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject,:grade,:topic,:actor)",
          "INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:version,:task,1,'Exact historical','problem','short_text',50,'approved',:actor)",
          "INSERT INTO task_skill_links(task_version_id,skill_id,weight,is_primary) VALUES (:version,:skill,1,true)",
          "INSERT INTO class_groups(id,name,created_by) VALUES (:group,'G',:actor)",
          "INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,'PII name')",
          "INSERT INTO assessments(id,title,created_by) VALUES (:assessment,'A',:actor)",
          "INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES (:variant,:assessment,'V',1)",
          "INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item,:variant,:version,1,2.50),(:item2,:variant,:version,2,3.75)",
          "INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by) VALUES (:assignment,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 hour',:actor)",
          "INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id) VALUES (:participant,:assignment,:student,:variant)",
          "INSERT INTO student_submissions(id,assignment_participant_id,attempt_no,status,submitted_at) VALUES (:submission,:participant,1,'submitted',clock_timestamp())",
          "INSERT INTO student_answers(submission_id,assessment_item_id,raw_answer,normalized_answer) VALUES (:submission,:item,CAST(:raw AS jsonb),CAST(:normalized AS jsonb))")
        values={**ids,"raw":'{"values":[2,1],"text":" A "}',"normalized":'{"stored":["A",2]}' }
        for sql in sqls: await c.execute(text(sql),values)
    yield engine,factory,ids
    await engine.dispose()

def request(ids,key="key",policy="policy",supersedes=None):
    return CheckingIntakeRequest(ids["submission"],key,"routing","checkers","threshold",policy,supersedes)

def service(factory): return CheckingIntakeService(SQLAlchemyCheckingIntakeUnitOfWorkFactory(factory))

async def counts(engine):
    async with engine.connect() as c:
        return (await c.scalar(text("SELECT count(*) FROM check_runs")),
                await c.scalar(text("SELECT count(*) FROM checker_events")))

async def terminalize(factory,run_id):
    async with factory() as s, s.begin(): await CheckingRepository(s).transition_run(run_id,1,"running")
    async with factory() as s, s.begin(): await CheckingRepository(s).transition_run(run_id,2,"completed")

async def test_submitted_snapshot_event_privacy_and_handoff_parity_without_normalization(context,monkeypatch):
    engine,factory,ids=context
    handoff=await AssessmentCheckingHandoffService(factory).get(ids["submission"])
    monkeypatch.setattr(phase3,"normalize_answer",lambda *_: (_ for _ in ()).throw(AssertionError("normalizer called")))
    run=await service(factory).create(request(ids))
    async with engine.connect() as c:
        row=(await c.execute(text("SELECT status::text,input_snapshot,input_fingerprint FROM check_runs WHERE id=:id"),{"id":run.id})).one()
        events=(await c.execute(text("SELECT details FROM checker_events WHERE check_run_id=:id"),{"id":run.id})).scalars().all()
    snapshot=row.input_snapshot; projected={"submission_id":snapshot["submission_id"],"submitted_at":snapshot["submitted_at"],
        "items":[{k:item[k] for k in ("assessment_item_id","task_version_id","position","points","answer_format","raw_answer","normalized_answer")} for item in snapshot["items"]]}
    assert projected==handoff.as_dict()
    assert snapshot["items"][0]["raw_answer"]=={"values":[2,1],"text":" A "}
    assert snapshot["items"][0]["normalized_answer"]=={"stored":["A",2]}
    assert snapshot["items"][1]["raw_answer"] is None and snapshot["items"][1]["normalized_answer"] is None
    encoded=str(snapshot)
    for permitted in (ids["submission"],ids["item"],ids["item2"],ids["version"]): assert str(permitted) in encoded
    for forbidden in (ids["student"],ids["participant"],ids["assignment"],ids["actor"]): assert str(forbidden) not in encoded
    for name in ("student_id","participant_id","assignment_id","actor_id","created_by"): assert name not in encoded
    assert len(events)==1 and all("raw_answer" not in x and "normalized_answer" not in x for x in events)

async def test_non_submitted_rolls_back_without_run_or_event(context):
    engine,factory,ids=context
    async with engine.begin() as c: await c.execute(text("UPDATE student_submissions SET status='draft',submitted_at=NULL WHERE id=:id"),{"id":ids["submission"]})
    with pytest.raises(SubmissionNotSubmitted): await service(factory).create(request(ids))
    assert await counts(engine)==(0,0)

async def test_same_key_replay_and_changed_policy_conflict(context):
    engine,factory,ids=context; intake=service(factory)
    first=await intake.create(request(ids)); second=await intake.create(request(ids)); assert first.id==second.id
    with pytest.raises(IdempotencyConflict): await intake.create(request(ids,policy="changed"))
    assert await counts(engine)==(1,1)

async def test_concurrent_same_key_has_one_run_and_initial_event(context):
    engine,factory,ids=context; first,second=await asyncio.gather(service(factory).create(request(ids)),service(factory).create(request(ids)))
    assert first.id==second.id
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM check_runs WHERE submission_id=:s AND request_key='key'"),{"s":ids["submission"]})==1
        assert await c.scalar(text("SELECT count(*) FROM check_runs WHERE submission_id=:s AND status IN ('pending','running')"),{"s":ids["submission"]})==1
        assert await c.scalar(text("SELECT count(*) FROM checker_events WHERE event_type='run_created'"))==1

async def test_concurrent_different_keys_has_one_typed_winner_and_no_orphan(context):
    engine,factory,ids=context
    results=await asyncio.gather(service(factory).create(request(ids,"one")),service(factory).create(request(ids,"two")),return_exceptions=True)
    assert sum(not isinstance(x,Exception) for x in results)==1
    assert sum(isinstance(x,ActiveRunConflict) for x in results)==1
    assert await counts(engine)==(1,1)

async def test_archive_close_later_version_rerun_preserves_history(context):
    engine,factory,ids=context; intake=service(factory); first=await intake.create(request(ids))
    async with engine.connect() as c:
        original=(await c.execute(text("SELECT input_snapshot,input_fingerprint FROM check_runs WHERE id=:id"),{"id":first.id})).one()
    original_snapshot=copy.deepcopy(original.input_snapshot)
    await terminalize(factory,first.id)
    async with engine.begin() as c:
        await c.execute(text("UPDATE tasks SET archived_at=clock_timestamp() WHERE id=:task"),ids)
        await c.execute(text("UPDATE task_versions SET status='archived' WHERE id=:version"),ids)
        await c.execute(text("UPDATE assignments SET status='closed',closed_at=clock_timestamp() WHERE id=:assignment"),ids)
        await c.execute(text("INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:new_version,:task,2,'New truth','problem','number',50,'draft',:actor)"),ids)
    rerun=await intake.create(request(ids,"rerun",supersedes=first.id))
    async with engine.connect() as c:
        rows=(await c.execute(text("SELECT id,input_snapshot,input_fingerprint,attempt_no,supersedes_run_id FROM check_runs ORDER BY attempt_no"))).all()
        event_counts=(await c.execute(text("SELECT check_run_id,count(*) FROM checker_events WHERE event_type='run_created' GROUP BY check_run_id"))).all()
    assert len(rows)==2 and rows[1].attempt_no==2 and rows[1].supersedes_run_id==first.id
    assert rows[0].input_snapshot==original_snapshot and rows[0].input_fingerprint==original.input_fingerprint
    assert rows[1].input_snapshot==original_snapshot and rows[1].input_fingerprint==original.input_fingerprint
    assert all(x["task_version_id"]==str(ids["version"]) for x in rows[1].input_snapshot["items"])
    assert str(ids["new_version"]) not in str(rows[1].input_snapshot)
    assert [x["points"] for x in rows[1].input_snapshot["items"]]==["2.50","3.75"]
    assert rows[1].input_snapshot["items"][1]["raw_answer"] is None
    assert rows[1].input_snapshot["items"][0]["normalized_answer"]=={"stored":["A",2]}
    assert sorted(count for _,count in event_counts)==[1,1]

async def test_explicit_rerun_and_invalid_supersedes_are_atomic(context):
    engine,factory,ids=context; intake=service(factory); first=await intake.create(request(ids))
    assert first.attempt_no==1
    with pytest.raises(InvalidCheckingInput): await intake.create(request(ids,"active",supersedes=first.id))
    with pytest.raises(InvalidCheckingInput): await intake.create(request(ids,"missing",supersedes=uuid4()))
    assert await counts(engine)==(1,1)
    await terminalize(factory,first.id)
    before_terminal_replay=await counts(engine)
    with pytest.raises(IdempotencyConflict): await intake.create(request(ids,"key",supersedes=first.id))
    assert await counts(engine)==before_terminal_replay
    other={x:uuid4() for x in ("student","participant","submission")}
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,'Other')"),{**ids,**other})
        await c.execute(text("INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id) VALUES (:participant,:assignment,:student,:variant)"),{**ids,**other})
        await c.execute(text("INSERT INTO student_submissions(id,assignment_participant_id,attempt_no,status,submitted_at) VALUES (:submission,:participant,1,'submitted',clock_timestamp())"),other)
    other_request=request({**ids,"submission":other["submission"]},"other")
    other_run=await intake.create(other_request); await terminalize(factory,other_run.id)
    before=await counts(engine)
    with pytest.raises(InvalidCheckingInput): await intake.create(request(ids,"foreign",supersedes=other_run.id))
    assert await counts(engine)==before
    second=await intake.create(request(ids,"rerun",supersedes=first.id))
    assert second.attempt_no==2 and second.supersedes_run_id==first.id

async def test_typed_materialization_failure_rolls_back(context,monkeypatch):
    engine,factory,ids=context
    def fail(*_): raise InvalidCheckingInput("controlled materialization failure")
    monkeypatch.setattr(intake_module,"build_snapshot",fail)
    with pytest.raises(InvalidCheckingInput,match="controlled"): await service(factory).create(request(ids))
    assert await counts(engine)==(0,0)
