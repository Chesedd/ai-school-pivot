"""One genuine Phase 4.10 vertical through PostgreSQL and production boundaries."""
import json
import os
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.checking_intake import CheckingIntakeRequest, CheckingIntakeService
from app.application.checking_llm_rubric import ConfidencePolicy, LLMRubricChecker, OUTPUT_SCHEMA_VERSION, SYSTEM_MESSAGE
from app.application.checking_provider import Pricing, PromptSpec, ProviderExecutionKey, ProviderExecutionService, ProviderResponse, ProviderUsage
from app.application.checking_results import ConfidenceGatePolicy, ResultReplayConflict
from app.application.checking_routing import CheckerRequest, CheckerType, route_snapshot
from app.application.checking_deterministic import execute_deterministic
from app.application.student_assessments import normalize_answer
from app.infrastructure.checking_intake_repository import SQLAlchemyCheckingIntakeUnitOfWorkFactory
from app.infrastructure.checking_repository import CheckingRepository, SQLAlchemyCheckingResultPersistence, SQLAlchemyProviderAttemptStore

URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Phase 4.10 requires a disposable database ending in _test")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]
TABLES = "cost_events,model_runs,checker_events,check_findings,check_results,prompt_versions,check_runs,assessment_audit_log,assessment_idempotency_keys,student_answers,student_submissions,assignment_participants,assignments,assessment_items,assessment_variants,assessments,students,class_groups,audit_log,task_error_links,rubric_items,rubrics,expected_solutions,accepted_answer_options,choice_option_rules,choice_scoring_policies,choice_options,accepted_answers,task_skill_links,task_versions,tasks,import_previews,typical_errors,skills,subtopics,topics,grades,subjects"


class SyntheticProvider:
    """The sole fake: an implementation of the production LLM provider port."""
    def __init__(self, rubric_item): self.rubric_item, self.calls = rubric_item, 0
    async def evaluate(self, request):
        self.calls += 1
        candidate={"schema_version":"llm_rubric_output_v1","rubric_items":[{"rubric_item_id":str(self.rubric_item),"status":"met","suggested_points":"2","evidence":[{"source":"student_answer","kind":"quote","quote":"force equals mass times acceleration","start":0,"end":36}],"limitations":[]}],"findings":[],"teacher_summary":"technical","student_feedback_draft":"technical","model_limitations":[]}
        return ProviderResponse("synthetic-response",json.dumps(candidate,separators=(",",":")),usage=ProviderUsage(17,11,3),latency_ms=999)


def gate(): return ConfidenceGatePolicy("phase410-gate-v1",Decimal("0.5000"),*(Decimal("0.1000"),)*4)


async def seed(engine):
    names=("actor","subject","grade","topic","subtopic","skill","error","group","student","assessment","variant","assignment","participant","submission","rubric","rubric_item")
    ids={name:uuid4() for name in names}; kinds=("exact","choice","numeric","expression","llm","unanswered","manual","insufficient")
    ids.update({f"task_{k}":uuid4() for k in kinds}); ids.update({f"version_{k}":uuid4() for k in kinds}); ids.update({f"item_{k}":uuid4() for k in kinds})
    ids.update({"accepted_exact":uuid4(),"accepted_choice":uuid4(),"accepted_numeric":uuid4(),"accepted_expression":uuid4(),"accepted_manual":uuid4(),"option_a":uuid4(),"option_b":uuid4(),"choice_policy":uuid4()})
    formats={"exact":"short_text","choice":"multiple_choice","numeric":"number","expression":"expression","llm":"long_text","unanswered":"short_text","manual":"expression","insufficient":"long_text"}
    raw={"exact":"Alpha","choice":[str(ids["option_a"])],"numeric":"12.5","expression":"x+1","llm":"force equals mass times acceleration","unanswered":None,"manual":"x","insufficient":"work"}
    async with engine.begin() as c:
        await c.execute(text(f"TRUNCATE {TABLES} CASCADE"))
        base=("INSERT INTO subjects(id,code,name) VALUES (:subject,'phase410','Phase 410')","INSERT INTO grades(id,number,name) VALUES (:grade,10,'10')","INSERT INTO topics(id,subject_id,grade_id,code,name) VALUES (:topic,:subject,:grade,'phase410','Phase 410')","INSERT INTO subtopics(id,topic_id,code,name) VALUES (:subtopic,:topic,'phase410','Phase 410')","INSERT INTO skills(id,subtopic_id,code,name) VALUES (:skill,:subtopic,'P410','Phase 410 skill')","INSERT INTO typical_errors(id,skill_id,code,title,description,severity) VALUES (:error,:skill,'P410E','Technical error','technical','major')","INSERT INTO class_groups(id,name,created_by) VALUES (:group,'PRIVATE CLASS',:actor)","INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,'PRIVATE PERSON')","INSERT INTO assessments(id,title,created_by) VALUES (:assessment,'PRIVATE ASSESSMENT',:actor)","INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES (:variant,:assessment,'A',1)","INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by) VALUES (:assignment,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 hour',:actor)","INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id,variant_assigned_at) VALUES (:participant,:assignment,:student,:variant,clock_timestamp())","INSERT INTO student_submissions(id,assignment_participant_id,attempt_no,status,submitted_at) VALUES (:submission,:participant,1,'submitted',clock_timestamp())")
        for sql in base: await c.execute(text(sql),ids)
        for position,k in enumerate(kinds,1):
            await c.execute(text("INSERT INTO tasks(id,subject_id,grade_id,topic_id,subtopic_id,created_by) VALUES (:task,:subject,:grade,:topic,:subtopic,:actor)"),{**ids,"task":ids[f"task_{k}"]})
            await c.execute(text("INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by,approved_by,approved_at) VALUES (:version,:task,1,:statement,'problem',:format,50,'approved',:actor,:actor,clock_timestamp())"),{**ids,"version":ids[f"version_{k}"],"task":ids[f"task_{k}"],"statement":f"PRIVATE TASK {k}","format":formats[k]})
            await c.execute(text("INSERT INTO task_skill_links(task_version_id,skill_id,weight,is_primary) VALUES (:version,:skill,1,true)"),{**ids,"version":ids[f"version_{k}"]})
            await c.execute(text("INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item,:variant,:version,:position,2)"),{**ids,"item":ids[f"item_{k}"],"version":ids[f"version_{k}"],"position":position})
            if raw[k] is not None:
                normalized=normalize_answer(formats[k],raw[k])
                await c.execute(text("INSERT INTO student_answers(submission_id,assessment_item_id,raw_answer,normalized_answer) VALUES (:submission,:item,CAST(:raw AS jsonb),CAST(:normalized AS jsonb))"),{**ids,"item":ids[f"item_{k}"],"raw":json.dumps(raw[k]),"normalized":json.dumps(normalized)})
        answers=(("exact","accepted_exact","Alpha","text","Alpha",None,"exact_text_v1"),("numeric","accepted_numeric","12.5","decimal",None,Decimal("12.5"),"decimal_v1"),("expression","accepted_expression","x+1","expression","x+1",None,"expression_identity_v1"),("manual","accepted_manual","x","expression","x",None,"expression_identity_v2"))
        for kind,key,value,value_kind,canonical_text,canonical_decimal,policy in answers:
            await c.execute(text("INSERT INTO accepted_answers(id,task_version_id,answer_value,value_kind,canonical_text,canonical_decimal,absolute_tolerance,relative_tolerance,normalization_policy_code,normalization_policy_version) VALUES (:id,:version,:value,:kind,:ct,:cd,0,0,:policy,1)"),{"id":ids[key],"version":ids[f"version_{kind}"],"value":value,"kind":value_kind,"ct":canonical_text,"cd":canonical_decimal,"policy":policy})
        await c.execute(text("INSERT INTO choice_options(id,task_version_id,option_key,content,order_index) VALUES (:option_a,:version_choice,'a','PRIVATE OPTION A',0),(:option_b,:version_choice,'b','PRIVATE OPTION B',1)"),ids)
        await c.execute(text("INSERT INTO accepted_answers(id,task_version_id,answer_value,value_kind) VALUES (:accepted_choice,:version_choice,'a','choice_set')"),ids)
        await c.execute(text("INSERT INTO accepted_answer_options(accepted_answer_id,choice_option_id,task_version_id) VALUES (:accepted_choice,:option_a,:version_choice)"),ids)
        await c.execute(text("INSERT INTO choice_scoring_policies(id,task_version_id,mode,policy_version) VALUES (:choice_policy,:version_choice,'all_or_nothing',1)"),ids)
        await c.execute(text("INSERT INTO expected_solutions(task_version_id,solution_text,final_answer,solution_steps_json) VALUES (:version_llm,'PRIVATE SOLUTION','PRIVATE EXPECTED','[]'::jsonb)"),ids)
        await c.execute(text("INSERT INTO rubrics(id,task_version_id,max_score,grading_mode,notes) VALUES (:rubric,:version_llm,2,'points','PRIVATE RUBRIC NOTES')"),ids)
        await c.execute(text("INSERT INTO rubric_items(id,rubric_id,criterion,max_points,required,order_index) VALUES (:rubric_item,:rubric,'PRIVATE RUBRIC PROSE',2,true,0)"),ids)
        await c.execute(text("INSERT INTO task_error_links(task_version_id,typical_error_id,detection_hint) VALUES (:version_llm,:error,'PRIVATE DETECTION')"),ids)
    return ids,kinds


async def test_phase410_real_postgresql_production_vertical():
    engine=create_async_engine(URL); factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        ids,kinds=await seed(engine)
        intake=CheckingIntakeService(SQLAlchemyCheckingIntakeUnitOfWorkFactory(factory))
        run=await intake.create(CheckingIntakeRequest(ids["submission"],"phase410-real-vertical","routing-v1","checker-v1",gate().semantic_version,"fake-provider-v1"))
        async with factory() as session,session.begin(): running=await CheckingRepository(session).transition_run(run.id,run.row_version,"running")
        async with engine.connect() as c:
            frozen=(await c.execute(text("SELECT input_snapshot,input_fingerprint FROM check_runs WHERE id=:r"),{"r":run.id})).one()
        snapshot=frozen.input_snapshot; assert [x["assessment_item_id"] for x in snapshot["items"]]==[str(ids[f"item_{k}"]) for k in kinds]
        decisions=route_snapshot(snapshot); provider=SyntheticProvider(ids["rubric_item"])
        service=ProviderExecutionService(SQLAlchemyProviderAttemptStore(factory),provider,sleeper=lambda _: _nothing(),jitter=lambda:0,monotonic=iter((1,1.125)).__next__)
        prompt=PromptSpec("checking.llm-rubric.phase410","1.0.0",SYSTEM_MESSAGE,OUTPUT_SCHEMA_VERSION)
        drafts=[]
        for item,decision in zip(snapshot["items"],decisions):
            checkers=None
            if decision.checker_type is CheckerType.LLM_RUBRIC and decision.execution_required:
                checker=LLMRubricChecker(service,ProviderExecutionKey(run.id,ids["item_llm"]),provider_id="synthetic-port",model_id="phase410",prompt=prompt,settings={"temperature":"0"},confidence_policy=ConfidencePolicy(gate().semantic_version,Decimal("0.7500"),("rubric_evidence",)),pricing=Pricing("USD","phase410-price-v1","test",Decimal("0.01"),Decimal("0.02"),Decimal("0.005")))
                checkers={CheckerType.LLM_RUBRIC:checker}
            drafts.append(await execute_deterministic(CheckerRequest(item,decision),checkers))
        persistence=SQLAlchemyCheckingResultPersistence(factory)
        first=await persistence.finalize(run.id,running.row_version,gate(),tuple(drafts))
        async with engine.connect() as c:
            results=(await c.execute(text("SELECT assessment_item_id,checker_type,result_status,reason_code,score_suggested,max_score,needs_human_review,review_reason,confidence_policy_version,confidence,confidence_details,validated_result,summary,student_feedback_draft,teacher_summary,model_limitations FROM check_results WHERE check_run_id=:r ORDER BY assessment_item_id"),{"r":run.id})).mappings().all()
            findings=(await c.execute(text("SELECT finding_type,rubric_item_id,typical_error_id,skill_id,snapshot_code,snapshot_title,snapshot_criterion,severity,confidence,evidence FROM check_findings f JOIN check_results r ON r.id=f.check_result_id WHERE r.check_run_id=:r ORDER BY f.id"),{"r":run.id})).mappings().all()
            events=(await c.execute(text("SELECT event_type,from_status,to_status,reason_code,details FROM checker_events WHERE check_run_id=:r ORDER BY occurred_at,id"),{"r":run.id})).mappings().all()
            attempts=(await c.execute(text("SELECT attempt_no,status,check_result_id,latency_ms,input_tokens,output_tokens,cached_tokens FROM model_runs WHERE check_run_id=:r ORDER BY attempt_no"),{"r":run.id})).mappings().all()
            costs=(await c.execute(text("SELECT amount FROM cost_events e JOIN model_runs m ON m.id=e.model_run_id WHERE m.check_run_id=:r"),{"r":run.id})).scalars().all()
        by_item={str(x["assessment_item_id"]):x for x in results}; assert len(by_item)==len(kinds)==8
        expected={"exact":("exact","correct","exact_match",Decimal("2"),False),"choice":("multiple_choice","correct","choice_match",Decimal("2"),False),"numeric":("numeric","correct","numeric_match",Decimal("2"),False),"expression":("structured_expression","correct","expression_identity_match",Decimal("2"),False),"llm":("llm_rubric","correct","llm_rubric_evaluated",Decimal("2"),True),"unanswered":("exact","incorrect","unanswered",Decimal("0"),False),"manual":("manual_required","manual_required","routing_manual_required",None,True),"insufficient":("manual_required","insufficient_rubric","routing_insufficient_rubric",None,True)}
        for kind,want in expected.items():
            row=by_item[str(ids[f"item_{kind}"])]; assert tuple(row[x] for x in ("checker_type","result_status","reason_code","score_suggested","needs_human_review"))==want; assert row["max_score"]==Decimal("2"); assert row["confidence_policy_version"]==gate().semantic_version; assert row["confidence"].as_tuple().exponent==-4; assert row["confidence_details"]["reasons"]==sorted(row["confidence_details"]["reasons"],key=lambda x:list(__import__('app.application.checking_results',fromlist=['ConfidenceReason']).ConfidenceReason).index(__import__('app.application.checking_results',fromlist=['ConfidenceReason']).ConfidenceReason(x)))
        assert first.run_status=="completed_with_review_required" and first.item_count==first.result_count==8 and first.review_required_count==3
        assert len(attempts)==1 and attempts[0]["attempt_no"]==1 and attempts[0]["status"]=="succeeded" and attempts[0]["check_result_id"] and attempts[0]["latency_ms"]==125 and tuple(attempts[0][x] for x in ("input_tokens","output_tokens","cached_tokens"))==(17,11,3)
        assert costs and all(type(x) is Decimal for x in costs); assert first.total_measured_provider_latency==125 and first.input_tokens==17 and first.output_tokens==11 and first.cached_tokens==3
        assert len(events)==12 and [x["event_type"] for x in events]==["run_created","run_transition","model_attempt",*(["result_recorded"]*8),"run_transition"]; assert sum(x["event_type"]=="run_transition" and x["to_status"] in ("completed","completed_with_review_required") for x in events)==1; assert provider.calls==1
        before=(len(results),len(findings),len(events),len(attempts)); replay=await persistence.finalize(run.id,running.row_version,gate(),tuple(drafts)); assert replay==first
        with pytest.raises(ResultReplayConflict): await persistence.finalize(run.id,running.row_version,gate(),tuple([replace(drafts[0],summary="changed"),*drafts[1:]]))
        async with engine.begin() as c:
            after=tuple((await c.execute(text("SELECT (SELECT count(*) FROM check_results WHERE check_run_id=:r),(SELECT count(*) FROM check_findings f JOIN check_results x ON x.id=f.check_result_id WHERE x.check_run_id=:r),(SELECT count(*) FROM checker_events WHERE check_run_id=:r),(SELECT count(*) FROM model_runs WHERE check_run_id=:r)"),{"r":run.id})).one()); assert after==before
            await c.execute(text("UPDATE assignments SET status='closed',closed_at=clock_timestamp(),closed_by=:actor WHERE id=:assignment"),ids); await c.execute(text("UPDATE tasks SET archived_at=clock_timestamp() WHERE id=:task"),{"task":ids["task_exact"]})
        async with engine.connect() as c: assert await c.scalar(text("SELECT input_fingerprint FROM check_runs WHERE id=:r"),{"r":run.id})==frozen.input_fingerprint
        private=["PRIVATE","raw_answer","normalized_answer","accepted_answers","statement","solution","rubric","raw_output",str(ids["student"]),str(ids["participant"]),str(ids["assignment"]),str(ids["group"])]
        persisted=json.dumps({"results":[dict(x) for x in results],"findings":[dict(x) for x in findings],"events":[dict(x) for x in events],"observability":first.__dict__},default=str,sort_keys=True)
        assert not any(value in persisted for value in private)
    finally: await engine.dispose()


async def _nothing(): pass
