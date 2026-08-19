"""Behavioral PostgreSQL acceptance for the Phase 4.7 provider boundary."""
import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.checking import CreateRunCommand, IdempotencyConflict, InvalidPersistenceCommand
from app.application.checking_provider import (AttemptDisposition, ContractProbe, Pricing,
    PromptSpec, ProviderExecutionKey, ProviderExecutionService, ProviderFailure,
    ProviderMessage, ProviderResponse, ProviderUsage, RequestConflict, build_request)
from app.infrastructure.checking_repository import CheckingRepository, SQLAlchemyProviderAttemptStore

URL=os.environ.get("TEST_DATABASE_URL","")
if URL and not URL.rsplit("/",1)[-1].split("?",1)[0].endswith("_test"):
    raise RuntimeError("provider tests require a database ending in _test")
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not URL,reason="TEST_DATABASE_URL is required")]
RAW='{"schema_version":"provider-contract-probe.v1","acknowledged":true}'


@pytest_asyncio.fixture
async def context():
    engine=create_async_engine(URL); factory=async_sessionmaker(engine,expire_on_commit=False)
    ids={key:uuid4() for key in ("actor","subject","grade","topic","task","version","group","student","assessment","variant","item","assignment","participant","submission")}
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE cost_events,model_runs,checker_events,check_findings,check_results,prompt_versions,check_runs,assessment_audit_log,assessment_idempotency_keys,student_answers,student_submissions,assignment_participants,assignments,assessment_items,assessment_variants,assessments,students,class_groups,audit_log,task_skill_links,task_versions,tasks,import_previews,skills,subtopics,topics,grades,subjects CASCADE"))
        sqls=("INSERT INTO subjects(id,code,name) VALUES (:subject,'provider-test','Provider')",
          "INSERT INTO grades(id,number,name) VALUES (:grade,7,'7')",
          "INSERT INTO topics(id,subject_id,grade_id,code,name) VALUES (:topic,:subject,:grade,'provider','Provider')",
          "INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject,:grade,:topic,:actor)",
          "INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:version,:task,1,'Synthetic','problem','short_text',50,'approved',:actor)",
          "INSERT INTO class_groups(id,name,created_by) VALUES (:group,'G',:actor)",
          "INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,'PRIVATE NAME')",
          "INSERT INTO assessments(id,title,created_by) VALUES (:assessment,'A',:actor)",
          "INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES (:variant,:assessment,'A',1)",
          "INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item,:variant,:version,1,2)",
          "INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by) VALUES (:assignment,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 hour',:actor)",
          "INSERT INTO assignment_participants(id,assignment_id,student_id) VALUES (:participant,:assignment,:student)",
          "INSERT INTO student_submissions(id,assignment_participant_id,attempt_no) VALUES (:submission,:participant,1)")
        for sql in sqls: await c.execute(text(sql),ids)
    snapshot={"submission_id":str(ids["submission"]),"items":[{"assessment_item_id":str(ids["item"]),"task_version_id":str(ids["version"]),"points":"2.00"}]}
    command=CreateRunCommand(ids["submission"],"provider", "a"*64,1,snapshot,"b"*64,"1","1","1","1","probe-v1")
    async with factory() as session,session.begin(): ids["run"]=(await CheckingRepository(session).create_run(command)).id
    yield engine,factory,ids
    await engine.dispose()


def parts(ids,*,settings=None,prompt=None):
    contract=ContractProbe(); prompt=prompt or PromptSpec("provider-probe","1.0.0","SYNTHETIC_TEMPLATE",contract.schema_version)
    request=build_request(provider_id="fake",model_id="probe-v1",prompt=prompt,contract=contract,
        messages=(ProviderMessage("system","SYNTHETIC_SYSTEM"),ProviderMessage("user","SYNTHETIC_USER")),settings=settings or {"temperature":"0.0"})
    return ProviderExecutionKey(ids["run"],ids["item"]),request,prompt,contract


class ScriptedProvider:
    def __init__(self,*script): self.script=list(script); self.calls=0
    async def evaluate(self,request):
        self.calls+=1; value=self.script.pop(0)
        if isinstance(value,Exception): raise value
        return value


def service(factory,provider,clock=None):
    return ProviderExecutionService(SQLAlchemyProviderAttemptStore(factory),provider,
        sleeper=lambda _:asyncio.sleep(0),jitter=lambda:0,monotonic=clock or __import__("time").monotonic)


async def rows(engine):
    async with engine.connect() as c:
        return (await c.execute(text("SELECT id,status::text,attempt_no,raw_output,validated_output,error_code,latency_ms,check_result_id FROM model_runs ORDER BY attempt_no"))).all()


async def test_prompt_identical_replay_and_changed_content_or_schema_conflicts(context):
    engine,factory,ids=context; spec=parts(ids)[2]
    async with factory() as s,s.begin(): first=(await CheckingRepository(s).register_prompt(spec)).id
    async with factory() as s,s.begin(): assert (await CheckingRepository(s).register_prompt(spec)).id==first
    for changed in (PromptSpec(spec.stable_name,spec.semantic_version,"CHANGED",spec.output_schema_version),PromptSpec(spec.stable_name,spec.semantic_version,spec.template_text,"changed-schema")):
        async with factory() as s,s.begin():
            with pytest.raises(IdempotencyConflict): await CheckingRepository(s).register_prompt(changed)
    async with engine.connect() as c: assert await c.scalar(text("SELECT count(*) FROM prompt_versions"))==1


async def test_retired_prompt_blocks_new_execution(context):
    engine,factory,ids=context; key,request,prompt,contract=parts(ids)
    async with factory() as s,s.begin(): row=await CheckingRepository(s).register_prompt(prompt); created=row.created_at; prompt_id=row.id
    async with factory() as s,s.begin(): await CheckingRepository(s).retire_prompt(prompt_id,created)
    provider=ScriptedProvider(ProviderResponse("never",RAW))
    with pytest.raises(InvalidPersistenceCommand): await service(factory,provider).execute(key,request,prompt,contract)
    assert provider.calls==0 and await rows(engine)==[]


async def test_success_persists_exact_output_safe_event_cost_and_measured_latency(context):
    engine,factory,ids=context; key,request,prompt,contract=parts(ids); clock=iter([1.0,1.125])
    response=ProviderResponse("provider-request",RAW,usage=ProviderUsage(10,2,1),latency_ms=999)
    pricing=Pricing("USD","2026-08","local",Decimal("0.01"),Decimal("0.02"),Decimal("0.005"))
    outcome=await service(factory,ScriptedProvider(response),lambda:next(clock)).execute(key,request,prompt,contract,pricing)
    data=await rows(engine); assert outcome.state=="succeeded" and len(data)==1
    assert data[0].status=="succeeded" and data[0].raw_output==RAW and data[0].validated_output["candidate"]["acknowledged"] is True
    assert data[0].latency_ms==125 and data[0].check_result_id is None
    async with engine.connect() as c:
        event=await c.scalar(text("SELECT details FROM checker_events WHERE event_type='model_attempt'")); cost=(await c.execute(text("SELECT amount,input_tokens,output_tokens,cached_tokens FROM cost_events"))).one()
        assert set(event)=={"model_run_id","attempt_no"} and tuple(cost)==(Decimal("0.14500000"),10,2,1)
        assert await c.scalar(text("SELECT count(*) FROM check_results"))==0
        telemetry=str(event)+str(cost); assert RAW not in telemetry and "SYNTHETIC" not in telemetry and "PRIVATE NAME" not in telemetry


async def test_transient_retry_has_three_contiguous_attempts_then_success_and_replays(context):
    engine,factory,ids=context; key,request,prompt,contract=parts(ids)
    provider=ScriptedProvider(ProviderFailure("transport"),ProviderFailure("provider_5xx"),ProviderResponse("r",RAW))
    runner=service(factory,provider); assert (await runner.execute(key,request,prompt,contract)).state=="succeeded"
    assert provider.calls==3 and [(x.status,x.attempt_no) for x in await rows(engine)]==[("failed",1),("failed",2),("succeeded",3)]
    assert (await runner.execute(key,request,prompt,contract)).state=="succeeded" and provider.calls==3


async def test_exhausted_transient_replays_without_more_calls(context):
    engine,factory,ids=context; key,request,prompt,contract=parts(ids)
    provider=ScriptedProvider(*[ProviderFailure("timeout") for _ in range(3)]); runner=service(factory,provider)
    assert (await runner.execute(key,request,prompt,contract)).state=="failed" and provider.calls==3
    assert (await runner.execute(key,request,prompt,contract)).state=="failed" and provider.calls==3


async def test_invalid_json_has_two_attempts(context):
    engine,factory,ids=context; key,request,prompt,contract=parts(ids)
    provider=ScriptedProvider(ProviderResponse("r1","not-json"),ProviderResponse("r2","still-not-json"))
    assert (await service(factory,provider).execute(key,request,prompt,contract)).state=="invalid"
    assert provider.calls==2 and [x.attempt_no for x in await rows(engine)]==[1,2]


@pytest.mark.parametrize("value,code",[(ProviderResponse("r",'{"schema_version":"provider-contract-probe.v1","acknowledged":1}'),"schema_invalid"),(ProviderFailure("authentication"),"authentication"),(ProviderFailure("content_blocked"),"content_blocked"),(ProviderFailure("unknown"),"unknown")])
async def test_nonretryable_response_or_failure_has_one_attempt(context,value,code):
    engine,factory,ids=context; key,request,prompt,contract=parts(ids); provider=ScriptedProvider(value)
    outcome=await service(factory,provider).execute(key,request,prompt,contract)
    assert outcome.error_code==code and provider.calls==1 and len(await rows(engine))==1


async def test_different_fingerprint_conflicts_and_running_replay_is_in_progress(context):
    engine,factory,ids=context; key,request,prompt,contract=parts(ids); store=SQLAlchemyProviderAttemptStore(factory)
    claimed=await store.replay_or_claim(key,request,prompt,3); assert claimed.disposition is AttemptDisposition.CLAIMED
    provider=ScriptedProvider(ProviderResponse("never",RAW))
    assert (await service(factory,provider).execute(key,request,prompt,contract)).state=="in_progress" and provider.calls==0
    changed=parts(ids,settings={"temperature":"0.1"})[1]
    with pytest.raises(RequestConflict): await service(factory,provider).execute(key,changed,prompt,contract)
    assert provider.calls==0 and len(await rows(engine))==1


async def test_concurrent_identical_claims_create_one_row_and_event(context):
    engine,factory,ids=context; key,request,prompt,contract=parts(ids); store=SQLAlchemyProviderAttemptStore(factory)
    first,second=await asyncio.gather(store.replay_or_claim(key,request,prompt,3),store.replay_or_claim(key,request,prompt,3))
    assert {first.disposition,second.disposition}=={AttemptDisposition.CLAIMED,AttemptDisposition.RUNNING_EXISTING}
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM model_runs"))==1
        assert await c.scalar(text("SELECT count(*) FROM checker_events WHERE event_type='model_attempt'"))==1


async def test_crash_after_terminal_commit_replays_and_history_is_immutable(context):
    engine,factory,ids=context; key,request,prompt,contract=parts(ids); first=ScriptedProvider(ProviderResponse("r",RAW,usage=ProviderUsage(1,1)))
    pricing=Pricing("USD","v1","local",Decimal(".1"),Decimal(".2"),Decimal("0"))
    await service(factory,first).execute(key,request,prompt,contract,pricing) # caller observation may be lost here
    never=ScriptedProvider(ProviderResponse("never",RAW)); assert (await service(factory,never).execute(key,request,prompt,contract,pricing)).state=="succeeded" and never.calls==0
    row=(await rows(engine))[0]
    async with engine.begin() as c:
        for sql in ("UPDATE model_runs SET error_code='changed' WHERE id=:id","DELETE FROM model_runs WHERE id=:id","UPDATE cost_events SET amount=9 WHERE model_run_id=:id","DELETE FROM cost_events WHERE model_run_id=:id"):
            with pytest.raises((IntegrityError,DBAPIError)):
                async with c.begin_nested(): await c.execute(text(sql),{"id":row.id})
