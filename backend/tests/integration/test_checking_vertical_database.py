"""One real PostgreSQL vertical through the complete Checking production path."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.checking_intake import CheckingIntakeRequest, CheckingIntakeService
from app.application.checking_llm_rubric import ConfidencePolicy, LLMRubricChecker, OUTPUT_SCHEMA_VERSION, SYSTEM_MESSAGE
from app.application.checking_provider import (Pricing, PromptSpec, ProviderExecutionKey,
    ProviderExecutionService, ProviderFailure, ProviderResponse, ProviderUsage)
from app.application.checking_results import ConfidenceGatePolicy, ResultReplayConflict
from app.application.checking_routing import (CheckerOutcome, CheckerRequest, CheckerType,
    ResultReason, route_snapshot)
from app.application.checking_deterministic import execute_deterministic
from app.application.student_assessments import normalize_answer
from app.infrastructure.checking_intake_repository import SQLAlchemyCheckingIntakeUnitOfWorkFactory
from app.infrastructure.checking_repository import (CheckingRepository,
    SQLAlchemyCheckingResultPersistence, SQLAlchemyProviderAttemptStore)

URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Phase 4.10 requires a disposable database ending in _test")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]
TABLES = """cost_events,model_runs,checker_events,check_findings,check_results,
prompt_versions,check_runs,assessment_audit_log,assessment_idempotency_keys,
student_answers,student_submissions,assignment_participants,assignments,
assessment_items,assessment_variants,assessments,students,class_groups,audit_log,
choice_option_rules,choice_scoring_policies,accepted_answer_options,choice_options,
accepted_answers,task_error_links,rubric_items,rubrics,expected_solutions,
task_skill_links,task_versions,tasks,typical_errors,skills,
subtopics,topics,grades,subjects""".replace("\n", "")


class SyntheticProvider:
    """The only synthetic boundary: injected through the LLM provider port."""
    def __init__(self, script): self.script, self.calls = list(script), 0
    async def evaluate(self, request):
        self.calls += 1; value = self.script.pop(0)
        if isinstance(value, Exception): raise value
        return value


class SyntheticClock:
    def __init__(self): self.values = iter((10.000, 10.125, 20.000, 20.250, 30.000, 30.375))
    def __call__(self): return next(self.values)


async def _no_sleep(_): return None
def _normalized(answer_format, raw):
    return json.dumps(normalize_answer(answer_format, raw), ensure_ascii=False, separators=(",", ":"))


async def test_phase410_real_postgresql_production_vertical():
    engine = create_async_engine(URL); factory = async_sessionmaker(engine, expire_on_commit=False)
    names = ("actor", "subject", "grade", "topic", "subtopic", "skill", "typical_error",
        "group", "student", "assessment", "variant", "assignment", "participant", "submission",
        "rubric", "rubric_item", "expected_solution", "choice_policy", "choice_a", "choice_b",
        "choice_c", "choice_answer", "exact_answer", "numeric_answer", "expression_answer",
        "manual_answer", "unanswered_answer")
    ids = {name: uuid4() for name in names}
    item_names = ("exact", "choice", "numeric", "expression", "manual", "llm", "unanswered", "insufficient")
    for name in item_names:
        ids[f"task_{name}"], ids[f"version_{name}"], ids[f"item_{name}"] = uuid4(), uuid4(), uuid4()
    tasks = (
        ("exact", "problem", "short_text", "PRIVATE_TASK_STATEMENT_EXACT", Decimal("2.00")),
        ("choice", "test", "multiple_choice", "PRIVATE_TASK_STATEMENT_CHOICE", Decimal("3.00")),
        ("numeric", "calculation", "number", "PRIVATE_TASK_STATEMENT_NUMERIC", Decimal("2.50")),
        ("expression", "calculation", "expression", "PRIVATE_TASK_STATEMENT_EXPRESSION", Decimal("1.50")),
        ("manual", "calculation", "expression", "PRIVATE_TASK_STATEMENT_MANUAL", Decimal("1.50")),
        ("llm", "essay", "long_text", "PRIVATE_TASK_STATEMENT_LLM", Decimal("4.00")),
        ("unanswered", "problem", "short_text", "PRIVATE_TASK_STATEMENT_UNANSWERED", Decimal("1.00")),
        ("insufficient", "calculation", "number", "PRIVATE_TASK_STATEMENT_INSUFFICIENT", Decimal("1.00")),
    )
    raw_answers = {"exact": "EXACT_PRIVATE_ANSWER",
        "choice": [str(ids["choice_a"]), str(ids["choice_c"])], "numeric": "420042.00",
        "expression": "PRIVATE_EXPRESSION_ANSWER", "manual": "PRIVATE_MANUAL_EXPRESSION",
        "llm": "PRIVATE_NORMALIZED_ANSWER", "insufficient": "700007"}
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {TABLES} CASCADE"))
            for statement in (
                "INSERT INTO subjects(id,code,name,normalized_name) VALUES (:subject,'phase410','Phase 410','phase 410')",
                "INSERT INTO grades(id,number,name,normalized_name) VALUES (:grade,10,'10','10')",
                "INSERT INTO topics(id,subject_id,grade_id,code,name,normalized_name) VALUES (:topic,:subject,:grade,'phase410','Phase 410','phase 410')",
                "INSERT INTO subtopics(id,topic_id,code,name,normalized_name) VALUES (:subtopic,:topic,'phase410','Phase 410','phase 410')",
                "INSERT INTO skills(id,subtopic_id,code,name,normalized_name) VALUES (:skill,:subtopic,'skill_technical_v1','technical_skill_v1','technical_skill_v1')",
                "INSERT INTO typical_errors(id,skill_id,code,title,description,severity) VALUES (:typical_error,:skill,'error_technical_v1','PRIVATE_TYPICAL_ERROR_TITLE','PRIVATE_TYPICAL_ERROR_DESCRIPTION','high')",
                "INSERT INTO class_groups(id,name,created_by) VALUES (:group,'PRIVATE_GROUP',:actor)",
                "INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group,'PRIVATE_PERSON')",
                "INSERT INTO assessments(id,title,created_by) VALUES (:assessment,'PRIVATE_ASSESSMENT',:actor)",
                "INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES (:variant,:assessment,'A',1)"):
                await connection.execute(text(statement), ids)
            for position, (name, task_type, answer_format, statement, points) in enumerate(tasks, 1):
                values = {**ids, "task": ids[f"task_{name}"], "version": ids[f"version_{name}"],
                    "item": ids[f"item_{name}"], "position": position, "task_type": task_type,
                    "answer_format": answer_format, "statement": statement, "points": points}
                await connection.execute(text("INSERT INTO tasks(id,subject_id,grade_id,topic_id,subtopic_id,created_by) VALUES (:task,:subject,:grade,:topic,:subtopic,:actor)"), values)
                await connection.execute(text("INSERT INTO task_versions(id,task_id,version_no,statement,task_type,answer_format,difficulty,status,created_by,approved_by,approved_at) VALUES (:version,:task,1,:statement,CAST(:task_type AS task_type),CAST(:answer_format AS answer_format),50,'approved',:actor,:actor,clock_timestamp())"), values)
                await connection.execute(text("INSERT INTO task_skill_links(task_version_id,skill_id,weight,is_primary) VALUES (:version,:skill,1,true)"), values)
                await connection.execute(text("INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:item,:variant,:version,:position,:points)"), values)
            accepted_rows = (
                ("exact_answer", "version_exact", "text", "EXACT_PRIVATE_ANSWER", None, "exact_text_v1"),
                ("numeric_answer", "version_numeric", "decimal", None, Decimal("420042"), "decimal_v1"),
                ("expression_answer", "version_expression", "expression", "PRIVATE_EXPRESSION_ANSWER", None, "expression_identity_v1"),
                ("manual_answer", "version_manual", "expression", "DIFFERENT_PRIVATE_EXPRESSION", None, "expression_identity_v1"),
                ("unanswered_answer", "version_unanswered", "text", "PRIVATE_UNANSWERED_ACCEPTED", None, "exact_text_v1"))
            for answer, version, kind, canonical_text, canonical_decimal, policy in accepted_rows:
                await connection.execute(text("INSERT INTO accepted_answers(id,task_version_id,answer_value,value_kind,canonical_text,canonical_decimal,absolute_tolerance,relative_tolerance,normalization_policy_code,normalization_policy_version) VALUES (:answer,:version,:answer_value,:kind,:canonical_text,:canonical_decimal,:absolute,:relative,:policy,1)"),
                    {"answer": ids[answer], "version": ids[version], "answer_value": canonical_text or str(canonical_decimal),
                     "kind": kind, "canonical_text": canonical_text, "canonical_decimal": canonical_decimal,
                     "absolute": Decimal("0") if kind == "decimal" else None,
                     "relative": Decimal("0") if kind == "decimal" else None, "policy": policy})
            for order, key in enumerate(("choice_a", "choice_b", "choice_c")):
                await connection.execute(text("INSERT INTO choice_options(id,task_version_id,option_key,content,order_index) VALUES (:option,:version,:key,:content,:order)"),
                    {"option": ids[key], "version": ids["version_choice"], "key": key,
                     "content": f"PRIVATE_OPTION_{key}", "order": order})
            await connection.execute(text("INSERT INTO accepted_answers(id,task_version_id,answer_value,value_kind) VALUES (:answer,:version,'technical-choice','choice_set')"),
                {"answer": ids["choice_answer"], "version": ids["version_choice"]})
            for key in ("choice_a", "choice_b"):
                await connection.execute(text("INSERT INTO accepted_answer_options(accepted_answer_id,choice_option_id,task_version_id) VALUES (:answer,:option,:version)"),
                    {"answer": ids["choice_answer"], "option": ids[key], "version": ids["version_choice"]})
            await connection.execute(text("INSERT INTO choice_scoring_policies(id,task_version_id,mode,policy_version) VALUES (:policy,:version,'per_option',1)"),
                {"policy": ids["choice_policy"], "version": ids["version_choice"]})
            for key, role, weight in (("choice_a", "correct", "0.600000"), ("choice_b", "correct", "0.400000"), ("choice_c", "distractor", "-0.200000")):
                await connection.execute(text("INSERT INTO choice_option_rules(policy_id,choice_option_id,task_version_id,role,weight) VALUES (:policy,:option,:version,:role,:weight)"),
                    {"policy": ids["choice_policy"], "option": ids[key], "version": ids["version_choice"],
                     "role": role, "weight": Decimal(weight)})
            await connection.execute(text("INSERT INTO expected_solutions(id,task_version_id,solution_text,final_answer,solution_steps_json) VALUES (:expected_solution,:version,'PRIVATE_EXPECTED_SOLUTION','PRIVATE_FINAL_ANSWER',CAST(:steps AS jsonb))"),
                {"expected_solution": ids["expected_solution"], "version": ids["version_llm"], "steps": json.dumps(["PRIVATE_SOLUTION_STEP"])})
            await connection.execute(text("INSERT INTO rubrics(id,task_version_id,max_score,grading_mode,notes) VALUES (:rubric,:version,2,'points','PRIVATE_RUBRIC_PROSE')"),
                {"rubric": ids["rubric"], "version": ids["version_llm"]})
            await connection.execute(text("INSERT INTO rubric_items(id,rubric_id,criterion,max_points,required,common_failure,order_index) VALUES (:item,:rubric,'criterion_v1',2,true,'PRIVATE_COMMON_FAILURE',0)"),
                {"item": ids["rubric_item"], "rubric": ids["rubric"]})
            await connection.execute(text("INSERT INTO task_error_links(task_version_id,typical_error_id,detection_hint) VALUES (:version,:error,'PRIVATE_DETECTION_HINT')"),
                {"version": ids["version_llm"], "error": ids["typical_error"]})
            for statement in (
                "INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by) VALUES (:assignment,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 hour',:actor)",
                "INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id,variant_assigned_at) VALUES (:participant,:assignment,:student,:variant,clock_timestamp())",
                "INSERT INTO student_submissions(id,assignment_participant_id,attempt_no,status,submitted_at) VALUES (:submission,:participant,1,'submitted',clock_timestamp())"):
                await connection.execute(text(statement), ids)
            formats = {name: fmt for name, _, fmt, _, _ in tasks}
            for name, raw in raw_answers.items():
                await connection.execute(text("INSERT INTO student_answers(submission_id,assessment_item_id,raw_answer,normalized_answer) VALUES (:submission,:item,CAST(:raw AS jsonb),CAST(:normalized AS jsonb))"),
                    {"submission": ids["submission"], "item": ids[f"item_{name}"],
                     "raw": json.dumps(raw, ensure_ascii=False), "normalized": _normalized(formats[name], raw)})

        intake = CheckingIntakeService(SQLAlchemyCheckingIntakeUnitOfWorkFactory(factory))
        run = await intake.create(CheckingIntakeRequest(ids["submission"], "phase-4.10.3",
            "checking_routing_contract_v1", "checking_checkers_v1", "confidence_v1",
            "checking_prompt_model_policy_v1"))
        ids["run"] = run.id
        async with engine.connect() as connection:
            persisted = (await connection.execute(text("SELECT input_snapshot,input_fingerprint,status::text,row_version FROM check_runs WHERE id=:run"), {"run": run.id})).mappings().one()
        snapshot = persisted["input_snapshot"]; original_snapshot = json.loads(json.dumps(snapshot))
        original_fingerprint = persisted["input_fingerprint"]
        assert persisted["status"] == "pending" and len(snapshot["items"]) == 8
        assert [item["assessment_item_id"] for item in snapshot["items"]] == [str(ids[f"item_{name}"]) for name in item_names]
        for item in snapshot["items"]:
            if item["raw_answer"] is not None:
                assert item["normalized_answer"] == normalize_answer(item["answer_format"], item["raw_answer"])
        async with factory() as session, session.begin():
            running = await CheckingRepository(session).transition_run(run.id, run.row_version, "running")

        candidate = {"schema_version": OUTPUT_SCHEMA_VERSION,
            "rubric_items": [{"rubric_item_id": str(ids["rubric_item"]), "status": "partial",
                "suggested_points": "1", "evidence": [], "limitations": []}],
            "findings": [{"finding_type": "rubric_miss", "rubric_item_id": str(ids["rubric_item"]),
                "typical_error_id": str(ids["typical_error"]), "skill_id": None,
                "message": "PRIVATE_PROVIDER_CANDIDATE_PROSE"}],
            "teacher_summary": "PRIVATE_PROVIDER_TEACHER_PROSE",
            "student_feedback_draft": "PRIVATE_PROVIDER_STUDENT_PROSE", "model_limitations": []}
        provider = SyntheticProvider((
            ProviderResponse("invalid-attempt", "not-json", usage=ProviderUsage(5, 2, 1)),
            ProviderFailure("transport"),
            ProviderResponse("success-attempt", json.dumps(candidate, separators=(",", ":")), usage=ProviderUsage(17, 11, 3))))
        service = ProviderExecutionService(SQLAlchemyProviderAttemptStore(factory), provider,
            sleeper=_no_sleep, jitter=lambda: 0, monotonic=SyntheticClock())
        llm_checker = LLMRubricChecker(service, ProviderExecutionKey(run.id, ids["item_llm"]),
            provider_id="phase410-synthetic", model_id="phase410-synthetic-v1",
            prompt=PromptSpec("checking.llm-rubric", "1.0.0", SYSTEM_MESSAGE, OUTPUT_SCHEMA_VERSION),
            settings={"temperature": "0", "seed": 4103, "max_output_tokens": 1000},
            confidence_policy=ConfidencePolicy("confidence_v1", Decimal("0.7500"), ("rubric_evidence",)),
            pricing=Pricing("USD", "price-v1", "phase410-test", Decimal("0.01"), Decimal("0.02"), Decimal("0.005")))
        decisions = route_snapshot(snapshot)
        assert [x.assessment_item_id for x in decisions] == [x["assessment_item_id"] for x in snapshot["items"]]
        drafts = []
        for item, decision in zip(snapshot["items"], decisions):
            checkers = {CheckerType.LLM_RUBRIC: llm_checker} if decision.checker_type is CheckerType.LLM_RUBRIC and decision.execution_required else None
            drafts.append(await execute_deterministic(CheckerRequest(item, decision), checkers))
        drafts = tuple(drafts); assert provider.calls == 3
        gate = ConfidenceGatePolicy("confidence_v1", Decimal("0.5000"), Decimal("0.1000"),
            Decimal("0.1000"), Decimal("0.1000"), Decimal("0.1000"))
        persistence = SQLAlchemyCheckingResultPersistence(factory)
        first = await persistence.finalize(run.id, running.row_version, gate, drafts)

        async with engine.connect() as connection:
            run_row = (await connection.execute(text("SELECT status::text,input_snapshot,input_fingerprint,row_version FROM check_runs WHERE id=:run"), {"run": run.id})).mappings().one()
            result_rows = (await connection.execute(text("SELECT i.position,r.id,r.assessment_item_id,r.checker_type::text,r.result_status::text,r.reason_code,r.score_suggested,r.max_score,r.needs_human_review,r.review_reason,r.confidence_policy_version,r.confidence,r.confidence_details,r.validated_result FROM check_results r JOIN assessment_items i ON i.id=r.assessment_item_id WHERE r.check_run_id=:run ORDER BY i.position"), {"run": run.id})).mappings().all()
            finding_rows = (await connection.execute(text("SELECT f.finding_type::text,f.rubric_item_id,f.typical_error_id,f.skill_id,f.snapshot_code,f.snapshot_title,f.snapshot_criterion,f.severity::text,f.confidence,f.evidence,e.skill_id AS source_skill_id FROM check_findings f JOIN check_results r ON r.id=f.check_result_id LEFT JOIN typical_errors e ON e.id=f.typical_error_id WHERE r.check_run_id=:run ORDER BY f.id"), {"run": run.id})).mappings().all()
            event_rows = (await connection.execute(text("SELECT event_type::text,from_status::text,to_status::text,reason_code,details FROM checker_events WHERE check_run_id=:run ORDER BY occurred_at,id"), {"run": run.id})).mappings().all()
            model_rows = (await connection.execute(text("SELECT status::text,attempt_no,check_result_id,provider_request_id,latency_ms,input_tokens,output_tokens,cached_tokens,error_code FROM model_runs WHERE check_run_id=:run ORDER BY attempt_no"), {"run": run.id})).mappings().all()
            cost_rows = (await connection.execute(text("SELECT m.attempt_no,c.currency,c.amount,c.input_tokens,c.output_tokens,c.cached_tokens,c.pricing_version,c.pricing_source FROM cost_events c JOIN model_runs m ON m.id=c.model_run_id WHERE m.check_run_id=:run ORDER BY m.attempt_no"), {"run": run.id})).mappings().all()
        assert run_row["status"] == "completed_with_review_required"
        assert run_row["input_snapshot"] == original_snapshot and run_row["input_fingerprint"] == original_fingerprint
        assert len(result_rows) == len(snapshot["items"]) == 8
        assert [x["assessment_item_id"] for x in result_rows] == [UUID(x["assessment_item_id"]) for x in snapshot["items"]]
        expected = (
            ("exact", "correct", "exact_match", Decimal("2.00"), Decimal("2.00"), False, None, Decimal("1.0000"), ["deterministic_proof"]),
            ("multiple_choice", "partially_correct", "choice_partial", Decimal("1.20"), Decimal("3.00"), False, None, Decimal("1.0000"), ["deterministic_proof"]),
            ("numeric", "correct", "numeric_match", Decimal("2.50"), Decimal("2.50"), False, None, Decimal("1.0000"), ["deterministic_proof"]),
            ("structured_expression", "correct", "expression_identity_match", Decimal("1.50"), Decimal("1.50"), False, None, Decimal("1.0000"), ["deterministic_proof"]),
            ("structured_expression", "manual_required", "expression_equivalence_unproven", None, Decimal("1.50"), True, "expression_equivalence_unproven", Decimal("0.0000"), ["manual_required", "below_review_threshold"]),
            ("llm_rubric", "partially_correct", "llm_rubric_evaluated", Decimal("2.00"), Decimal("4.00"), True, "llm_human_review_required", Decimal("0.6500"), ["llm_calibrated_base", "missing_rubric_evidence"]),
            ("exact", "incorrect", "unanswered", Decimal("0.00"), Decimal("1.00"), False, None, Decimal("1.0000"), ["unanswered"]),
            ("manual_required", "insufficient_rubric", "routing_insufficient_rubric", None, Decimal("1.00"), True, "missing_typed_accepted_answer", Decimal("0.0000"), ["insufficient_rubric", "below_review_threshold"]))
        for row, values in zip(result_rows, expected):
            assert (row["checker_type"], row["result_status"], row["reason_code"], row["score_suggested"],
                row["max_score"], row["needs_human_review"], row["review_reason"], row["confidence"],
                row["confidence_details"]["reasons"]) == values
            assert row["confidence_policy_version"] == "confidence_v1"
            assert re.fullmatch(r"[a-z0-9_]{1,64}", row["reason_code"])
            assert row["confidence_details"]["effective"] == format(row["confidence"], ".4f")
        assert len(finding_rows) == 1
        finding = finding_rows[0]
        assert (finding["finding_type"], finding["rubric_item_id"], finding["typical_error_id"], finding["source_skill_id"]) == ("rubric", ids["rubric_item"], ids["typical_error"], ids["skill"])
        assert finding["skill_id"] is None and finding["snapshot_criterion"] == "criterion_v1"
        assert finding["snapshot_code"] is None and finding["snapshot_title"] is None
        assert finding["severity"] == "major" and finding["confidence"] == Decimal("0.6500")
        assert set(finding["evidence"]) == {"schema_version", "source_finding_kind", "rubric_item_id", "typical_error_id", "skill_id", "reason_code"}
        expected_events = ["run_created", "run_transition"] + ["model_attempt"] * 3 + ["result_recorded"] * 8 + ["run_transition"]
        assert [x["event_type"] for x in event_rows] == expected_events
        assert [(x["from_status"], x["to_status"]) for x in event_rows if x["event_type"] == "run_transition"] == [("pending", "running"), ("running", "completed_with_review_required")]
        assert sum(x["to_status"] == "completed_with_review_required" for x in event_rows) == 1
        assert all(len(json.dumps(x["details"], sort_keys=True)) <= 1000 for x in event_rows)
        llm_result_id = next(x["id"] for x in result_rows if x["checker_type"] == "llm_rubric")
        assert [(x["status"], x["attempt_no"], x["error_code"]) for x in model_rows] == [("invalid", 1, "invalid_json"), ("failed", 2, "transport"), ("succeeded", 3, None)]
        assert all(x["check_result_id"] == llm_result_id for x in model_rows)
        assert [x["latency_ms"] for x in model_rows] == [125, 250, 375]
        assert [(x["input_tokens"], x["output_tokens"], x["cached_tokens"]) for x in model_rows] == [(5, 2, 1), (0, 0, 0), (17, 11, 3)]
        assert [(x["attempt_no"], x["amount"]) for x in cost_rows] == [(1, Decimal("0.09500000")), (3, Decimal("0.40500000"))]
        assert first.item_count == first.result_count == 8 and first.review_required_count == 3 and first.finding_count == 1
        assert first.result_counts_by_status == (("correct", 3), ("incorrect", 1), ("insufficient_rubric", 1), ("manual_required", 1), ("partially_correct", 2))
        assert first.result_counts_by_checker_type == (("exact", 2), ("llm_rubric", 1), ("manual_required", 1), ("multiple_choice", 1), ("numeric", 1), ("structured_expression", 2))
        assert first.result_counts_by_reason == (("choice_partial", 1), ("exact_match", 1),
            ("expression_equivalence_unproven", 1), ("expression_identity_match", 1),
            ("llm_rubric_evaluated", 1), ("numeric_match", 1),
            ("routing_insufficient_rubric", 1), ("unanswered", 1))
        assert first.model_attempt_counts_by_status == (("failed", 1), ("invalid", 1), ("succeeded", 1))
        assert first.provider_retry_count == 3 and first.total_measured_provider_latency == 750
        assert (first.input_tokens, first.output_tokens, first.cached_tokens) == (22, 13, 4)
        assert first.costs == (("USD", "price-v1", "phase410-test", "0.50000000"),)

        async def counts():
            async with engine.connect() as connection:
                return tuple((await connection.execute(text("SELECT (SELECT count(*) FROM check_results WHERE check_run_id=:run),(SELECT count(*) FROM check_findings f JOIN check_results r ON r.id=f.check_result_id WHERE r.check_run_id=:run),(SELECT count(*) FROM checker_events WHERE check_run_id=:run),(SELECT count(*) FROM model_runs WHERE check_run_id=:run),(SELECT count(*) FROM model_runs WHERE check_run_id=:run AND check_result_id IS NOT NULL)"), {"run": run.id})).one())
        before_replay = await counts(); replay = await persistence.finalize(run.id, running.row_version, gate, drafts)
        assert replay == first and await counts() == before_replay
        changed = replace(drafts[0], outcome=CheckerOutcome.INCORRECT,
            reason_code=ResultReason.EXACT_MISMATCH, score_suggested=Decimal("0.00"))
        with pytest.raises(ResultReplayConflict):
            await persistence.finalize(run.id, running.row_version, gate, (changed,) + drafts[1:])
        assert await counts() == before_replay
        privacy_payload = {"results": [{"validated_result": x["validated_result"], "confidence_details": x["confidence_details"]} for x in result_rows],
            "findings": [dict(x) for x in finding_rows], "events": [dict(x) for x in event_rows], "observability": asdict(first)}
        serialized = json.dumps(privacy_payload, ensure_ascii=False, sort_keys=True, default=str)
        forbidden = ("EXACT_PRIVATE_ANSWER", "420042.00", "PRIVATE_EXPRESSION_ANSWER",
            "PRIVATE_MANUAL_EXPRESSION", "700007", str(ids["choice_a"]), str(ids["choice_c"]),
            "PRIVATE_NORMALIZED_ANSWER", "PRIVATE_UNANSWERED_ACCEPTED",
            "PRIVATE_TASK_STATEMENT", "PRIVATE_EXPECTED_SOLUTION", "PRIVATE_FINAL_ANSWER", "PRIVATE_SOLUTION_STEP",
            "PRIVATE_RUBRIC_PROSE", "PRIVATE_COMMON_FAILURE", "PRIVATE_PROVIDER_CANDIDATE_PROSE",
            "PRIVATE_PROVIDER_TEACHER_PROSE", "PRIVATE_PROVIDER_STUDENT_PROSE", "not-json", "PRIVATE_PERSON",
            str(ids["student"]), str(ids["participant"]), str(ids["assignment"]), str(ids["group"]))
        assert not any(secret in serialized for secret in forbidden)

        historical_results = json.loads(json.dumps([dict(x) for x in result_rows], default=str))
        historical_findings = json.loads(json.dumps([dict(x) for x in finding_rows], default=str))
        historical_events = json.loads(json.dumps([dict(x) for x in event_rows], default=str))
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE assignments SET status='closed',closed_at=clock_timestamp(),closed_by=:actor WHERE id=:assignment"), ids)
            await connection.execute(text("UPDATE tasks SET archived_at=clock_timestamp() WHERE id=:task"), {"task": ids["task_llm"]})
        async with engine.connect() as connection:
            after = (await connection.execute(text("SELECT input_snapshot,input_fingerprint FROM check_runs WHERE id=:run"), {"run": run.id})).mappings().one()
            after_results = (await connection.execute(text("SELECT i.position,r.id,r.assessment_item_id,r.checker_type::text,r.result_status::text,r.reason_code,r.score_suggested,r.max_score,r.needs_human_review,r.review_reason,r.confidence_policy_version,r.confidence,r.confidence_details,r.validated_result FROM check_results r JOIN assessment_items i ON i.id=r.assessment_item_id WHERE r.check_run_id=:run ORDER BY i.position"), {"run": run.id})).mappings().all()
            after_findings = (await connection.execute(text("SELECT f.finding_type::text,f.rubric_item_id,f.typical_error_id,f.skill_id,f.snapshot_code,f.snapshot_title,f.snapshot_criterion,f.severity::text,f.confidence,f.evidence,e.skill_id AS source_skill_id FROM check_findings f JOIN check_results r ON r.id=f.check_result_id LEFT JOIN typical_errors e ON e.id=f.typical_error_id WHERE r.check_run_id=:run ORDER BY f.id"), {"run": run.id})).mappings().all()
            after_events = (await connection.execute(text("SELECT event_type::text,from_status::text,to_status::text,reason_code,details FROM checker_events WHERE check_run_id=:run ORDER BY occurred_at,id"), {"run": run.id})).mappings().all()
        assert after["input_snapshot"] == original_snapshot and after["input_fingerprint"] == original_fingerprint
        assert json.loads(json.dumps([dict(x) for x in after_results], default=str)) == historical_results
        assert json.loads(json.dumps([dict(x) for x in after_findings], default=str)) == historical_findings
        assert json.loads(json.dumps([dict(x) for x in after_events], default=str)) == historical_events
    finally:
        await engine.dispose()
