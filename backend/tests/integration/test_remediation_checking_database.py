"""Executable C9C scenarios through remediation submit and shared Checking."""
import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.checking import IdempotencyConflict
from app.application.checking_deterministic import execute_deterministic
from app.application.checking_intake import CheckingIntakeRequest, CheckingIntakeService
from app.application.checking_items import CheckingItemKind
from app.application.checking_llm_rubric import (ConfidencePolicy, LLMRubricChecker,
    OUTPUT_SCHEMA_VERSION, SYSTEM_MESSAGE)
from app.application.checking_provider import (Pricing, PromptSpec, ProviderExecutionKey,
    ProviderExecutionService, ProviderFailure, ProviderResponse, ProviderUsage)
from app.application.checking_results import ConfidenceGatePolicy
from app.application.checking_routing import CheckerRequest, CheckerType, route_snapshot
from app.application.remediation_execution import RemediationExecutionService
from app.infrastructure.checking_intake_repository import SQLAlchemyCheckingIntakeUnitOfWorkFactory
from app.infrastructure.checking_repository import (CheckingRepository,
    SQLAlchemyCheckingResultPersistence, SQLAlchemyProviderAttemptStore)
from tests.integration.c10a_postgres import rolled_back_connection
from tests.integration.c9ab_fixtures import seed_execution_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class FakeProvider:
    def __init__(self, script=()): self.script, self.calls = list(script), 0
    async def evaluate(self, request):
        self.calls += 1
        value = self.script.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


async def _no_sleep(_): pass


async def _submitted_world(connection, *, llm=False):
    ids = await seed_execution_world(connection)
    ids.update({"accepted": uuid4(), "legacy_accepted": uuid4(),
                "skill": uuid4(), "error": uuid4()})
    # Item zero adds exact-match methodology. Item one's shared historical rubric
    # can be routed through the LLM checker when requested by the scenario.
    await connection.execute(text("""
      INSERT INTO accepted_answers(id,task_version_id,answer_value,value_kind,canonical_text,
        normalization_policy_code,normalization_policy_version)
      VALUES (:accepted,:version_0_0,'forty-two','text','forty-two','exact_text_v1',1)
    """), ids)
    if not llm:
        await connection.execute(text("""
          INSERT INTO accepted_answers(id,task_version_id,answer_value,value_kind)
          VALUES (:legacy_accepted,:version_0_1,'historical answer','legacy_untyped')
        """), ids)
    factory = async_sessionmaker(bind=connection, expire_on_commit=False,
                                 join_transaction_mode="create_savepoint")
    execution = RemediationExecutionService(factory)
    await execution.start(ids["plan_0"], ids["student_0"])
    await execution.save_answer(ids["plan_0"], ids["plan_item_0_0"], ids["student_0"], "forty-two")
    await execution.save_answer(ids["plan_0"], ids["plan_item_0_1"], ids["student_0"], "long explanation")
    submitted = await execution.submit(ids["plan_0"], ids["student_0"])
    run = (await connection.execute(text("SELECT * FROM check_runs WHERE submission_id=:s"),
                                    {"s": submitted["submission_id"]})).mappings().one()
    return ids, factory, execution, submitted, run


async def _run_checkers(factory, run, provider=None):
    async with factory() as session, session.begin():
        running = await CheckingRepository(session).transition_run(run["id"], run["row_version"], "running")
    decisions = route_snapshot(run["input_snapshot"])
    drafts = []
    for item, decision in zip(run["input_snapshot"]["items"], decisions):
        checkers = None
        if decision.checker_type is CheckerType.LLM_RUBRIC and decision.execution_required:
            service = ProviderExecutionService(SQLAlchemyProviderAttemptStore(factory), provider,
                sleeper=_no_sleep, jitter=lambda: 0)
            checker = LLMRubricChecker(service, ProviderExecutionKey(run["id"],
                UUID(decision.assessment_item_id), CheckingItemKind.REMEDIATION),
                provider_id="fake", model_id="fake-v1",
                prompt=PromptSpec("checking.llm-rubric", "1.0.0", SYSTEM_MESSAGE,
                                  OUTPUT_SCHEMA_VERSION), settings={"temperature": "0"},
                confidence_policy=ConfidencePolicy(
                    "confidence_v1", Decimal("0.5"), ("rubric_evidence",)),
                pricing=Pricing("USD", "test-v1", "test", Decimal("0"), Decimal("0"),
                                Decimal("0")))
            checkers = {CheckerType.LLM_RUBRIC: checker}
        drafts.append(await execute_deterministic(CheckerRequest(item, decision), checkers))
    gate = ConfidenceGatePolicy("checking_confidence_v1", Decimal("0.5"),
        Decimal("0.1"), Decimal("0.1"), Decimal("0.1"), Decimal("0.1"))
    result = await SQLAlchemyCheckingResultPersistence(factory).finalize(
        run["id"], running.row_version, gate, tuple(drafts))
    return result


async def test_submit_deterministic_persistence_events_and_replay_have_remediation_identity():
    async with rolled_back_connection() as connection:
        ids, factory, execution, submitted, run = await _submitted_world(connection)
        provider = FakeProvider()
        final = await _run_checkers(factory, run, provider)
        rows = (await connection.execute(text("""
          SELECT assessment_item_id,remediation_plan_item_id,task_version_id,max_score,result_status::text
          FROM check_results WHERE check_run_id=:run ORDER BY remediation_plan_item_id
        """), {"run": run["id"]})).mappings().all()
        assert len(rows) == 2 and final.run_status == "completed_with_review_required"
        assert all(row["assessment_item_id"] is None for row in rows)
        exact = next(row for row in rows if row["remediation_plan_item_id"] == ids["plan_item_0_0"])
        assert (exact["task_version_id"], exact["max_score"], exact["result_status"]) == (
            ids["version_0_0"], Decimal("1.00"), "correct")
        events = (await connection.execute(text("""
          SELECT event_type::text,assessment_item_id,remediation_plan_item_id FROM checker_events
          WHERE check_run_id=:run ORDER BY occurred_at,id
        """), {"run": run["id"]})).mappings().all()
        assert all(e["assessment_item_id"] is None for e in events)
        assert all(e["remediation_plan_item_id"] is None for e in events
                   if e["event_type"] in {"run_created", "run_transition"})
        assert {e["remediation_plan_item_id"] for e in events if e["event_type"] == "result_recorded"} == {
            ids["plan_item_0_0"], ids["plan_item_0_1"]}
        assert provider.calls == 0
        assert await connection.scalar(text("SELECT count(*) FROM model_runs WHERE check_run_id=:run"),
                                       {"run": run["id"]}) == 0
        repeated = await execution.submit(ids["plan_0"], ids["student_0"])
        assert repeated["submission_id"] == submitted["submission_id"]
        assert await connection.scalar(text("SELECT count(*) FROM check_runs WHERE submission_id=:s"),
                                       {"s": submitted["submission_id"]}) == 1
        assert await connection.scalar(text("SELECT count(*) FROM student_submissions WHERE remediation_plan_id=:p"),
                                       {"p": ids["plan_0"]}) == 1


async def test_intake_replay_and_changed_request_conflict_are_shared_contracts():
    async with rolled_back_connection() as connection:
        ids, factory, _, submitted, run = await _submitted_world(connection)
        request = CheckingIntakeRequest(submitted["submission_id"], run["request_key"],
            run["routing_version"], run["checker_set_version"], run["threshold_policy_version"],
            run["prompt_model_policy_version"])
        intake = CheckingIntakeService(SQLAlchemyCheckingIntakeUnitOfWorkFactory(factory))
        assert (await intake.create(request)).id == run["id"]
        with pytest.raises(IdempotencyConflict):
            await intake.create(CheckingIntakeRequest(submitted["submission_id"], run["request_key"],
                run["routing_version"], run["checker_set_version"], run["threshold_policy_version"],
                "changed-policy"))


@pytest.mark.parametrize("script,expected_attempts", [
    (["success"], [(1, "succeeded", None)]),
    ([ProviderFailure("transport"), "success"], [(1, "failed", "transport"), (2, "succeeded", None)]),
])
async def test_fake_llm_uses_shared_prompt_provider_retry_and_model_identity(script, expected_attempts):
    async with rolled_back_connection() as connection:
        ids, factory, _, _, run = await _submitted_world(connection, llm=True)
        candidate = {"schema_version": OUTPUT_SCHEMA_VERSION,
            "rubric_items": [{"rubric_item_id": str(ids["rubric_item"]), "status": "met",
                              "suggested_points": "3", "evidence": [], "limitations": []}],
            "findings": [{"finding_type": "rubric_miss",
                          "rubric_item_id": str(ids["rubric_item"]),
                          "typical_error_id": None, "skill_id": None,
                          "message": "bounded rubric finding"}],
            "teacher_summary": "teacher", "student_feedback_draft": "student",
            "model_limitations": []}
        responses = [ProviderResponse(
            f"fake-{n}", json.dumps(candidate), usage=ProviderUsage(1, 1, 0))
                     if value == "success" else value for n, value in enumerate(script)]
        provider = FakeProvider(responses)
        final = await _run_checkers(factory, run, provider)
        attempts = (await connection.execute(text("""
          SELECT attempt_no,status::text,error_code,assessment_item_id,remediation_plan_item_id
          FROM model_runs WHERE check_run_id=:run ORDER BY attempt_no
        """), {"run": run["id"]})).mappings().all()
        assert [(x["attempt_no"], x["status"], x["error_code"]) for x in attempts] == expected_attempts
        assert provider.calls == len(expected_attempts)
        assert all(x["assessment_item_id"] is None and
                   x["remediation_plan_item_id"] == ids["plan_item_0_1"] for x in attempts)
        result = (await connection.execute(text("""
          SELECT assessment_item_id,remediation_plan_item_id FROM check_results
          WHERE check_run_id=:run AND checker_type='llm_rubric'
        """), {"run": run["id"]})).one()
        assert result == (None, ids["plan_item_0_1"])
        finding = (await connection.execute(text("""
          SELECT f.rubric_item_id,r.remediation_plan_item_id
          FROM check_findings f JOIN check_results r ON r.id=f.check_result_id
          WHERE r.check_run_id=:run
        """), {"run": run["id"]})).one()
        assert finding == (ids["rubric_item"], ids["plan_item_0_1"])
        assert final.run_status in {"completed", "completed_with_review_required"}
