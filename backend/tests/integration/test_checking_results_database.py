"""Real PostgreSQL behavioral acceptance for Checking Phase 4.9."""
import asyncio, json, os
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID, uuid4
import pytest, pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.application.checking import CreateRunCommand, ConcurrentConflict, IdempotencyConflict, InvalidPersistenceCommand
from app.application.checking_results import ConfidenceGatePolicy, ConfidencePolicyConflict, InvalidCheckingResult, ResultReplayConflict
from app.application.checking_routing import CheckerOutcome, CheckerResultDraft, CheckerType, ResultReason
from app.infrastructure.checking_repository import CheckingRepository, SQLAlchemyCheckingResultPersistence

URL=os.environ.get("TEST_DATABASE_URL","")
if URL and not URL.rsplit("/",1)[-1].split("?",1)[0].endswith("_test"):
    raise RuntimeError("checking result tests require a disposable database ending in _test")
pytestmark=pytest.mark.asyncio

TABLES="cost_events,model_runs,checker_events,check_findings,check_results,prompt_versions,check_runs,assessment_audit_log,assessment_idempotency_keys,student_answers,student_submissions,assignment_participants,assignments,assessment_items,assessment_variants,assessments,students,class_groups,audit_log,task_error_links,rubric_items,rubrics,expected_solutions,task_skill_links,task_versions,tasks,import_previews,typical_errors,skills,subtopics,topics,grades,subjects"

@pytest_asyncio.fixture
async def db():
    if not URL: pytest.skip("TEST_DATABASE_URL is required")
    engine=create_async_engine(URL); factory=async_sessionmaker(engine,expire_on_commit=False)
    ids={k:uuid4() for k in ("actor","subject","grade","topic","subtopic","skill","error","task1","task2","version1","version2","rubric","rubric_item","group","student","assessment","variant","item1","item2","assignment","participant","submission")}
    async with engine.begin() as c:
        await c.execute(text(f"TRUNCATE {TABLES} CASCADE"))
        statements=(
          "INSERT INTO subjects(id,code,name) VALUES (:subject,'phase49','Phase 49')",
          "INSERT INTO grades(id,number,name) VALUES (:grade,9,'9')",
          "INSERT INTO topics(id,subject_id,grade_id,code,name) VALUES (:topic,:subject,:grade,'phase49','Phase 49')",
          "INSERT INTO subtopics(id,topic_id,code,name) VALUES (:subtopic,:topic,'phase49','Phase 49')",
          "INSERT INTO skills(id,subtopic_id,code,name) VALUES (:skill,:subtopic,'SK49','Skill 49')",
          "INSERT INTO typical_errors(id,skill_id,code,title,description,severity) VALUES (:error,:skill,'ERR49','Authored error','Technical description','high')",
          "INSERT INTO tasks(id,subject_id,grade_id,topic_id,subtopic_id,created_by) VALUES (:task1,:subject,:grade,:topic,:subtopic,:actor),(:task2,:subject,:grade,:topic,:subtopic,:actor)",
          "INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by,approved_by,approved_at) VALUES (:version1,:task1,1,'PRIVATE TASK ONE','problem','short_text',50,'approved',:actor,:actor,clock_timestamp()),(:version2,:task2,1,'PRIVATE TASK TWO','essay','long_text',50,'approved',:actor,:actor,clock_timestamp())",
          "INSERT INTO task_skill_links(task_version_id,skill_id,weight,is_primary) VALUES (:version2,:skill,1,true)",
          "INSERT INTO rubrics(id,task_version_id,max_score,grading_mode) VALUES (:rubric,:version2,2,'points')",
          "INSERT INTO rubric_items(id,rubric_id,criterion,max_points,required,order_index) VALUES (:rubric_item,:rubric,'PRIVATE RUBRIC PROSE',2,true,0)",
          "INSERT INTO task_error_links(task_version_id,typical_error_id,detection_hint) VALUES (:version2,:error,'PRIVATE DETECTION')",
          "INSERT INTO class_groups(id,name,created_by) VALUES (:group,'G49',:actor)",
          "INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,'PRIVATE PERSON')",
          "INSERT INTO assessments(id,title,created_by) VALUES (:assessment,'A49',:actor)",
          "INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES (:variant,:assessment,'A',1)",
          "INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item1,:variant,:version1,1,2),(:item2,:variant,:version2,2,2)",
          "INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by) VALUES (:assignment,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 hour',:actor)",
          "INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id,variant_assigned_at) VALUES (:participant,:assignment,:student,:variant,clock_timestamp())",
          "INSERT INTO student_submissions(id,assignment_participant_id,attempt_no,status,submitted_at) VALUES (:submission,:participant,1,'submitted',clock_timestamp())")
        for sql in statements: await c.execute(text(sql),ids)
    yield engine,factory,ids
    await engine.dispose()

def snapshot(ids):
    return {"submission_id":str(ids["submission"]),"items":[
      {"assessment_item_id":str(ids["item1"]),"task_version_id":str(ids["version1"]),"position":1,"points":"2.00","raw_answer":"PRIVATE RAW ANSWER","normalized_answer":{"text":"PRIVATE NORMALIZED"},"rubric_item_ids":[],"typical_error_ids":[],"skill_ids":[],"methodology":{"rubric":None,"typical_errors":[],"skills":[]}},
      {"assessment_item_id":str(ids["item2"]),"task_version_id":str(ids["version2"]),"position":2,"points":"2.00","raw_answer":"PRIVATE LLM RAW","normalized_answer":{"text":"PRIVATE LLM NORMALIZED"},"rubric_item_ids":[str(ids["rubric_item"])],"typical_error_ids":[str(ids["error"])],"skill_ids":[str(ids["skill"])],"methodology":{"statement":"PRIVATE TASK TWO","rubric":{"items":[{"id":str(ids["rubric_item"]),"criterion":"PRIVATE RUBRIC PROSE","required":True}]},"typical_errors":[{"id":str(ids["error"]),"skill_id":str(ids["skill"]),"code":"ERR49","title":"Authored error","severity":"critical"}],"skills":[{"id":str(ids["skill"]),"code":"SK49","name":"Skill 49"}]}}]}

def gate(version="gate-v1"): return ConfidenceGatePolicy(version,Decimal("0.5000"),Decimal("0.1000"),Decimal("0.1000"),Decimal("0.1000"),Decimal("0.1000"))
def deterministic(ids,outcome=CheckerOutcome.CORRECT,reason=ResultReason.EXACT_MATCH,score=Decimal("2.00")):
    return CheckerResultDraft(str(ids["item1"]),str(ids["version1"]),outcome,CheckerType.EXACT,"exact-v1",reason,score,Decimal("2.00"),Decimal("1.0000"),"Technical summary",None,None,False,None)
def manual(ids): return CheckerResultDraft(str(ids["item2"]),str(ids["version2"]),CheckerOutcome.MANUAL_REQUIRED,CheckerType.MANUAL_REQUIRED,"manual-v1",ResultReason.ROUTING_MANUAL_REQUIRED,None,Decimal("2.00"),Decimal("0.0000"),"Manual",None,None,True,"manual_required")
def llm(ids,*,outcome=CheckerOutcome.PARTIALLY_CORRECT,reason=ResultReason.LLM_RUBRIC_EVALUATED,score=Decimal("1.00"),finding=True):
    findings=(MappingProxyType({"finding_type":"rubric_miss","rubric_item_id":str(ids["rubric_item"]),"message":"PRIVATE PROVIDER MESSAGE"}),) if finding else ()
    rubric=(MappingProxyType({"rubric_item_id":str(ids["rubric_item"]),"status":"partial","evidence":(MappingProxyType({"source":"student_answer"}),),"limitations":()}),)
    evidence=MappingProxyType({"confidence_policy_version":"gate-v1","confidence_reason_codes":("calibrated",)}) if outcome is not CheckerOutcome.UNCLEAR else MappingProxyType({})
    return CheckerResultDraft(str(ids["item2"]),str(ids["version2"]),outcome,CheckerType.LLM_RUBRIC,"llm-v1",reason,score,Decimal("2.00"),Decimal("0.8000") if score is not None else Decimal("0.0000"),"Technical LLM",None,None,True,"llm_review",(),evidence,findings,rubric if score is not None else ())

async def running(db):
    engine,factory,ids=db; snap=snapshot(ids); command=CreateRunCommand(ids["submission"],str(uuid4()),"a"*64,1,snap,"b"*64,"checking_input_v1","routing-v1","checker-v1","gate-v1","prompt-v1")
    async with factory() as s,s.begin():
        row=await CheckingRepository(s).create_run(command); row=await CheckingRepository(s).transition_run(row.id,row.row_version,"running")
    ids["run"]=row.id; ids["row_version"]=row.row_version; return SQLAlchemyCheckingResultPersistence(factory),ids
async def finalize(db,drafts,policy=None):
    store,ids=await running(db); return store,ids,await store.finalize(ids["run"],ids["row_version"],policy or gate(),tuple(drafts(ids) if callable(drafts) else drafts))
async def counts(engine,run):
    async with engine.connect() as c:
      return tuple((await c.execute(text("SELECT (SELECT count(*) FROM check_results WHERE check_run_id=:r),(SELECT count(*) FROM check_findings f JOIN check_results x ON x.id=f.check_result_id WHERE x.check_run_id=:r),(SELECT count(*) FROM checker_events WHERE check_run_id=:r AND event_type='run_transition' AND to_status IN ('completed','completed_with_review_required'))"),{"r":run})).one())
async def terminal_attempt(db,ids,*,status="succeeded",attempt=1,cost=True):
    engine,_,_=db
    async with engine.begin() as c:
      prompt=await c.scalar(text("INSERT INTO prompt_versions(name,semantic_version,template_hash,output_schema_version,template_text) VALUES ('p49',:v,:h,'o1','technical') RETURNING id"),{"v":str(uuid4()),"h":uuid4().hex+uuid4().hex})
      model=await c.scalar(text("INSERT INTO model_runs(check_run_id,assessment_item_id,prompt_version_id,provider_id,model_id,settings_snapshot,request_fingerprint,attempt_no,timeout_ms,status,finished_at,validated_output,raw_output,latency_ms,input_tokens,output_tokens,cached_tokens) VALUES (:r,:i,:p,'fake','model', '{}'::jsonb,:f,:a,1000,:s,CASE WHEN :s='running' THEN NULL ELSE clock_timestamp() END,CASE WHEN :s='succeeded' THEN '{}'::jsonb ELSE NULL END,'PRIVATE RAW PROVIDER OUTPUT',125,10,4,2) RETURNING id"),{"r":ids["run"],"i":ids["item2"],"p":prompt,"f":uuid4().hex+uuid4().hex,"a":attempt,"s":status})
      if cost and status!="running": await c.execute(text("INSERT INTO cost_events(model_run_id,currency,input_tokens,output_tokens,cached_tokens,amount,pricing_version,pricing_source) VALUES (:m,'USD',10,4,2,1.25000000,'price-v1','test')"),{"m":model})
    return model

async def test_deterministic_batch_completes_without_review(db):
    store,ids,obs=await finalize(db,lambda i:(deterministic(i),replace(deterministic(i),assessment_item_id=str(i["item2"]),task_version_id=str(i["version2"]))))
    assert obs.run_status=="completed" and obs.review_required_count==0 and await counts(db[0],ids["run"])==(2,0,1)
    async with db[0].connect() as c: assert (await c.execute(text("SELECT score_suggested,needs_human_review FROM check_results WHERE check_run_id=:r ORDER BY created_at,id"),{"r":ids["run"]})).all()==[(Decimal("2.00"),False),(Decimal("2.00"),False)]
async def test_mixed_batch_completes_with_review(db):
    _,ids,obs=await finalize(db,lambda i:(deterministic(i),manual(i))); assert obs.run_status=="completed_with_review_required" and obs.review_required_count==1
    async with db[0].connect() as c: assert (await c.execute(text("SELECT needs_human_review,review_reason FROM check_results WHERE check_run_id=:r ORDER BY assessment_item_id"),{"r":ids["run"]})).all() in [[(False,None),(True,"manual_required")],[(True,"manual_required"),(False,None)]]
async def test_unclear_result_persists_null_score_and_confidence(db):
    _,ids,obs=await finalize(db,lambda i:(deterministic(i),llm(i,outcome=CheckerOutcome.UNCLEAR,reason=ResultReason.LLM_PROVIDER_FAILURE,score=None,finding=False)))
    async with db[0].connect() as c:
      row=(await c.execute(text("SELECT result_status::text,score_suggested,confidence,confidence_details,needs_human_review FROM check_results WHERE check_run_id=:r AND assessment_item_id=:i"),{"r":ids["run"],"i":ids["item2"]})).one(); assert row[0:3]==("unclear",None,Decimal("0.0000")) and row[3]["effective"]=="0.0000" and row[4]
async def test_exact_replay_has_no_duplicate_results_findings_or_events(db):
    store,ids,first=await finalize(db,lambda i:(deterministic(i),llm(i))); before=await counts(db[0],ids["run"]); second=await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),llm(ids))); assert first==second and before==await counts(db[0],ids["run"])==(2,1,1)
async def test_changed_replay_conflicts(db):
    store,ids,_=await finalize(db,lambda i:(deterministic(i),llm(i))); before=await counts(db[0],ids["run"])
    with pytest.raises(ResultReplayConflict): await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),llm(ids,finding=False)))
    assert await counts(db[0],ids["run"])==before
async def test_concurrent_identical_finalization_has_one_result_set(db):
    store,ids=await running(db); batch=(deterministic(ids),manual(ids)); results=await asyncio.gather(store.finalize(ids["run"],ids["row_version"],gate(),batch),store.finalize(ids["run"],ids["row_version"],gate(),batch),return_exceptions=True); assert all(not isinstance(x,Exception) for x in results) and await counts(db[0],ids["run"])==(2,0,1)
async def test_concurrent_different_finalization_has_one_winner(db):
    store,ids=await running(db); a=(deterministic(ids),manual(ids)); b=(deterministic(ids),llm(ids)); results=await asyncio.gather(store.finalize(ids["run"],ids["row_version"],gate(),a),store.finalize(ids["run"],ids["row_version"],gate(),b),return_exceptions=True); assert sum(not isinstance(x,Exception) for x in results)==1 and sum(isinstance(x,ResultReplayConflict) for x in results)==1 and (await counts(db[0],ids["run"]))[0]==2
async def test_invalid_finding_provenance_rolls_back_batch(db):
    store,ids=await running(db); bad=replace(llm(ids),findings=(MappingProxyType({"finding_type":"rubric_miss","rubric_item_id":str(uuid4()),"message":"x"}),))
    with pytest.raises(InvalidCheckingResult): await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),bad))
    assert await counts(db[0],ids["run"])==(0,0,0)
async def test_incomplete_item_batch_rolls_back(db):
    store,ids=await running(db)
    with pytest.raises(InvalidPersistenceCommand): await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),))
    assert await counts(db[0],ids["run"])==(0,0,0)
async def test_duplicate_item_batch_rolls_back(db):
    store,ids=await running(db)
    with pytest.raises(InvalidPersistenceCommand): await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),deterministic(ids)))
    assert await counts(db[0],ids["run"])==(0,0,0)
async def test_threshold_policy_mismatch_rolls_back(db):
    store,ids=await running(db)
    with pytest.raises(IdempotencyConflict): await store.finalize(ids["run"],ids["row_version"],gate("wrong"),(deterministic(ids),manual(ids)))
    assert await counts(db[0],ids["run"])==(0,0,0)
async def test_terminal_model_attempts_link_once_to_matching_result(db):
    store,ids=await running(db); model=await terminal_attempt(db,ids); await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),llm(ids)))
    async with db[0].connect() as c:
      row=(await c.execute(text("SELECT m.check_result_id,m.status::text,m.input_tokens,r.assessment_item_id FROM model_runs m JOIN check_results r ON r.id=m.check_result_id WHERE m.id=:m"),{"m":model})).one(); assert row[0] and row[1:]==("succeeded",10,ids["item2"])
async def test_model_attempt_reassignment_and_unlink_are_rejected(db):
    store,ids=await running(db); model=await terminal_attempt(db,ids); await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),llm(ids)))
    async with db[0].connect() as c: original=await c.scalar(text("SELECT check_result_id FROM model_runs WHERE id=:m"),{"m":model}); other=await c.scalar(text("SELECT id FROM check_results WHERE check_run_id=:r AND assessment_item_id=:i"),{"r":ids["run"],"i":ids["item1"]})
    for target in (None,other):
      async with db[0].begin() as c:
       with pytest.raises(DBAPIError): await c.execute(text("UPDATE model_runs SET check_result_id=:x WHERE id=:m"),{"x":target,"m":model})
    async with db[0].connect() as c: assert await c.scalar(text("SELECT check_result_id FROM model_runs WHERE id=:m"),{"m":model})==original
async def test_running_model_attempt_blocks_finalization(db):
    store,ids=await running(db); model=await terminal_attempt(db,ids,status="running",cost=False)
    with pytest.raises(InvalidPersistenceCommand): await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),llm(ids)))
    assert await counts(db[0],ids["run"])==(0,0,0)
async def test_observability_aggregates_attempts_tokens_latency_and_cost(db):
    store,ids=await running(db); await terminal_attempt(db,ids,attempt=1); await terminal_attempt(db,ids,attempt=2); obs=await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),llm(ids))); assert obs.model_attempt_counts_by_status==(("succeeded",2),) and obs.provider_retry_count==1 and obs.total_measured_provider_latency==250 and (obs.input_tokens,obs.output_tokens,obs.cached_tokens)==(20,8,4) and obs.costs==(("USD","price-v1","test","2.50000000"),)
async def test_observability_excludes_raw_output_answers_and_pii(db):
    store,ids=await running(db); await terminal_attempt(db,ids); obs=await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),llm(ids))); payload=json.dumps(obs.__dict__,default=str,sort_keys=True); forbidden=("PRIVATE","raw_answer","normalized_answer","student_id","participant_id","submission_id","assignment_id","provider_output"); assert not any(x in payload for x in forbidden)
async def test_archive_close_and_later_version_preserve_history(db):
    store,ids,obs=await finalize(db,lambda i:(deterministic(i),manual(i))); before=await counts(db[0],ids["run"])
    async with db[0].begin() as c:
      await c.execute(text("UPDATE tasks SET archived_at=clock_timestamp() WHERE id=:t"),{"t":ids["task1"]}); await c.execute(text("UPDATE task_versions SET status='archived' WHERE id=:v"),{"v":ids["version1"]}); await c.execute(text("UPDATE assignments SET status='closed',closed_at=clock_timestamp(),closed_by=:a WHERE id=:x"),{"a":ids["actor"],"x":ids["assignment"]}); await c.execute(text("INSERT INTO task_versions(task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:t,2,'LATER','problem','short_text',50,'draft',:a)"),{"t":ids["task1"],"a":ids["actor"]})
    replay=await store.finalize(ids["run"],ids["row_version"],gate(),(deterministic(ids),manual(ids))); assert replay==obs and await counts(db[0],ids["run"])==before
    async with db[0].connect() as c: assert await c.scalar(text("SELECT count(*) FROM check_results WHERE check_run_id=:r AND task_version_id=:v"),{"r":ids["run"],"v":ids["version1"]})==1
async def test_result_finding_and_event_history_is_immutable(db):
    _,ids,_=await finalize(db,lambda i:(deterministic(i),llm(i)))
    statements=("UPDATE check_results SET summary='changed' WHERE check_run_id=:r","DELETE FROM check_findings WHERE check_result_id IN (SELECT id FROM check_results WHERE check_run_id=:r)","DELETE FROM checker_events WHERE check_run_id=:r AND event_type='run_transition'","UPDATE check_runs SET input_fingerprint=:f,row_version=row_version+1 WHERE id=:r")
    for sql in statements:
      async with db[0].begin() as c:
       with pytest.raises(DBAPIError): await c.execute(text(sql),{"r":ids["run"],"f":"c"*64})
    assert await counts(db[0],ids["run"])==(2,1,1)
