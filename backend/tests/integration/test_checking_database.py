"""Real PostgreSQL proofs for the Phase 4.1 Checking foundation."""
import asyncio, os
from uuid import uuid4

import pytest, pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.application.checking import ActiveRunConflict, ConcurrentConflict, CreateRunCommand, IdempotencyConflict
from app.infrastructure.checking_repository import CheckingRepository

URL=os.environ.get("TEST_DATABASE_URL","")
if URL and not URL.rsplit("/",1)[-1].split("?",1)[0].endswith("_test"): raise RuntimeError("Checking tests require a database ending in _test")
pytestmark=[pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]

@pytest_asyncio.fixture
async def engine():
    value=create_async_engine(URL); yield value; await value.dispose()

@pytest_asyncio.fixture
async def seeded(engine):
    ids={key:uuid4() for key in ("actor","subject","grade","topic","task","version","group","student","assessment","variant","item","assignment","participant","submission")}
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE cost_events,model_runs,checker_events,check_findings,check_results,prompt_versions,check_runs,assessment_audit_log,assessment_idempotency_keys,student_answers,student_submissions,assignment_participants,assignments,assessment_items,assessment_variants,assessments,students,class_groups,audit_log,task_skill_links,task_versions,tasks,import_previews,skills,subtopics,topics,grades,subjects CASCADE"))
        statements=("INSERT INTO subjects(id,code,name) VALUES (:subject,'checking-test','Checking')","INSERT INTO grades(id,number,name) VALUES (:grade,7,'7')","INSERT INTO topics(id,subject_id,grade_id,code,name) VALUES (:topic,:subject,:grade,'checking','Checking')","INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject,:grade,:topic,:actor)","INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:version,:task,1,'S','problem','short_text',50,'approved',:actor)","INSERT INTO class_groups(id,name,created_by) VALUES (:group,'G',:actor)","INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,'Student')","INSERT INTO assessments(id,title,created_by) VALUES (:assessment,'A',:actor)","INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES (:variant,:assessment,'A',1)","INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item,:variant,:version,1,2)","INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by) VALUES (:assignment,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 hour',:actor)","INSERT INTO assignment_participants(id,assignment_id,student_id) VALUES (:participant,:assignment,:student)","INSERT INTO student_submissions(id,assignment_participant_id,attempt_no) VALUES (:submission,:participant,1)")
        for sql in statements: await c.execute(text(sql),ids)
    yield ids

def cmd(ids,key="key",hash="a"*64):
    snapshot={"submission_id":str(ids["submission"]),"items":[{"assessment_item_id":str(ids["item"]),"task_version_id":str(ids["version"]),"points":"2.00","rubric_item_ids":[],"typical_error_ids":[],"skill_ids":[]}]}
    return CreateRunCommand(ids["submission"],key,hash,1,snapshot,"b"*64,"1","1","1","1","none-v1")

async def create(engine,ids,key="key",hash="a"*64):
    async with AsyncSession(engine) as s:
        async with s.begin(): row=await CheckingRepository(s).create_run(cmd(ids,key,hash)); run_id=row.id
        return run_id

async def rejected(c,sql,values):
    with pytest.raises((IntegrityError,DBAPIError)):
        async with c.begin_nested(): await c.execute(text(sql),values)

async def test_schema_has_tables_indexes_enums_and_no_pii_columns(engine,seeded):
    async with engine.connect() as c:
        tables=set((await c.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE ANY(ARRAY['check_%','checker_%','prompt_versions','model_runs','cost_events'])"))).all())
        assert {"check_runs","check_results","check_findings","checker_events","prompt_versions","model_runs","cost_events"}<=tables
        cols=set((await c.scalars(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name IN ('check_runs','check_results','check_findings','checker_events','model_runs','cost_events')"))).all())
        assert not cols & {"student_id","participant_id","class_group_id","display_name"}

async def test_idempotency_replay_and_conflict(engine,seeded):
    first=await create(engine,seeded)
    async with AsyncSession(engine) as s:
        async with s.begin():
            assert (await CheckingRepository(s).create_run(cmd(seeded))).id==first
            with pytest.raises(IdempotencyConflict): await CheckingRepository(s).create_run(cmd(seeded,hash="c"*64))
    async with engine.connect() as c: assert await c.scalar(text("SELECT count(*) FROM checker_events WHERE check_run_id=:id"),{"id":first})==1

async def test_concurrent_same_key_creates_one_row(engine,seeded):
    results=await asyncio.gather(create(engine,seeded),create(engine,seeded))
    assert results[0]==results[1]

async def test_different_key_cannot_create_second_active_run(engine,seeded):
    await create(engine,seeded,"one")
    with pytest.raises(ActiveRunConflict): await create(engine,seeded,"two")

async def test_run_transition_is_cas_and_event_is_atomic(engine,seeded):
    run=await create(engine,seeded)
    async with AsyncSession(engine) as s:
        async with s.begin():
            running=await CheckingRepository(s).transition_run(run,1,"running")
            assert running.status=="running" and running.row_version==2
    async with AsyncSession(engine) as s:
        async with s.begin():
            with pytest.raises(ConcurrentConflict):
                await CheckingRepository(s).transition_run(run,1,"completed")
    async with engine.connect() as c:
        state=(await c.execute(text("SELECT status::text,row_version FROM check_runs WHERE id=:run"),{"run":run})).one()
        events=(await c.execute(text("SELECT from_status::text,to_status::text FROM checker_events WHERE check_run_id=:run AND event_type='run_transition' ORDER BY occurred_at,id"),{"run":run})).all()
        assert tuple(state) == ("running",2)
        assert [tuple(event) for event in events] == [("pending","running")]
        assert events[0][0] != events[0][1]
    async with AsyncSession(engine) as s:
        async with s.begin():
            completed=await CheckingRepository(s).transition_run(run,2,"completed")
            assert completed.status=="completed" and completed.row_version==3
    async with engine.connect() as c:
        events=(await c.execute(text("SELECT from_status::text,to_status::text FROM checker_events WHERE check_run_id=:run AND event_type='run_transition' ORDER BY occurred_at,id"),{"run":run})).all()
        assert [tuple(event) for event in events] == [("pending","running"),("running","completed")]
        assert await c.scalar(text("SELECT count(*) FROM checker_events WHERE check_run_id=:run"),{"run":run})==3

async def test_fk_score_unique_and_immutability_constraints(engine,seeded):
    run=await create(engine,seeded); values={**seeded,"run":run,"result":uuid4(),"missing":uuid4()}
    async with engine.begin() as c:
        await rejected(c,"DELETE FROM student_submissions WHERE id=:submission",values)
        await rejected(c,"UPDATE check_runs SET input_fingerprint=:hash WHERE id=:run",{**values,"hash":"c"*64})
        await rejected(c,"INSERT INTO check_results(id,check_run_id,assessment_item_id,task_version_id,checker_type,checker_version,schema_version,result_status,score_suggested,max_score,confidence,summary,needs_human_review,validated_result) VALUES (:result,:run,:missing,:version,'exact','1','1','correct',2,2,.9,'ok',false,'{}')",values)
        await c.execute(text("INSERT INTO check_results(id,check_run_id,assessment_item_id,task_version_id,checker_type,checker_version,schema_version,result_status,score_suggested,max_score,confidence,summary,needs_human_review,validated_result) VALUES (:result,:run,:item,:version,'exact','1','1','correct',2,2,.9,'ok',false,'{}')"),values)
        await rejected(c,"INSERT INTO check_results(check_run_id,assessment_item_id,task_version_id,checker_type,checker_version,schema_version,result_status,score_suggested,max_score,confidence,summary,needs_human_review,validated_result) VALUES (:run,:item,:version,'exact','1','1','incorrect',0,2,.9,'x',false,'{}')",values)
        await rejected(c,"UPDATE check_results SET summary='changed' WHERE id=:result",values); await rejected(c,"DELETE FROM check_results WHERE id=:result",values)

async def test_model_attempt_cost_and_prompt_guards(engine,seeded):
    run=await create(engine,seeded); values={**seeded,"run":run,"prompt":uuid4(),"model":uuid4(),"result":uuid4()}
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO prompt_versions(id,name,semantic_version,template_hash,output_schema_version,template_text) VALUES (:prompt,'p','1.0.0',repeat('a',64),'1','restricted')"),values)
        await c.execute(text("INSERT INTO model_runs(id,check_run_id,assessment_item_id,prompt_version_id,provider_id,model_id,settings_snapshot,request_fingerprint,attempt_no,timeout_ms) VALUES (:model,:run,:item,:prompt,'synthetic','m','{}',repeat('b',64),1,1000)"),values)
        await c.execute(text("UPDATE model_runs SET status='failed',finished_at=clock_timestamp(),error_code='timeout' WHERE id=:model"),values)
        await rejected(c,"UPDATE model_runs SET error_code='other' WHERE id=:model",values)
        await c.execute(text("INSERT INTO cost_events(model_run_id,currency,input_tokens,output_tokens,cached_tokens,amount,pricing_version,pricing_source) VALUES (:model,'USD',1,2,0,.01,'v1','synthetic')"),values)
        await rejected(c,"INSERT INTO cost_events(model_run_id,currency,input_tokens,output_tokens,cached_tokens,amount,pricing_version,pricing_source) VALUES (:model,'USD',1,2,0,.01,'v1','synthetic')",values)
        await rejected(c,"UPDATE prompt_versions SET template_text='changed' WHERE id=:prompt",values)
        await c.execute(text("UPDATE prompt_versions SET retired_at=clock_timestamp() WHERE id=:prompt"),values)
        await rejected(c,"UPDATE prompt_versions SET retired_at=clock_timestamp()+interval '1 second' WHERE id=:prompt",values)
