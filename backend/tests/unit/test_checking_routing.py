import asyncio
import copy
import json
from decimal import Decimal
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pytest

from app.application.checking_routing import (
    Checker, CheckerOutcome, CheckerRequest, CheckerResultDraft, CheckerType,
    ResultReason, RoutingDisposition, RoutingInputError, RoutingReason, route_snapshot,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
CANONICAL_INPUT_FIXTURE = FIXTURES_DIR / "checking_input_v1_canonical.json"


def uid(): return str(uuid4())


def answer(kind, **overrides):
    base={"id":uid(),"value_kind":kind,"canonical_text":None,"canonical_decimal":None,
          "absolute_tolerance":None,"relative_tolerance":None,"unit_code":None,
          "normalization_policy_code":None,"normalization_policy_version":None,"option_ids":[]}
    base.update(overrides); return base


def rubric(mode="points"):
    return {"id":uid(),"grading_mode":mode,"max_score":"2","items":[
        {"id":uid(),"order_index":0,"criterion":"correct","max_points":"2"}]}


def snapshot(fmt="short_text", task_type=None, answered=True):
    tasks={"single_choice":"test","multiple_choice":"test","short_text":"calculation",
           "number":"calculation","expression":"calculation","long_text":"essay"}
    method={"statement":"secret statement","task_type":task_type or tasks.get(fmt,"calculation"),
            "answer_format":fmt,"accepted_answers":[],"choice_options":[],
            "choice_scoring_policy":None,"expected_solution":None,"rubric":None}
    item={"assessment_item_id":uid(),"task_version_id":uid(),"points":"2.00","position":1,
          "answer_format":fmt,"raw_answer":{"secret":"student answer"} if answered else None,
          "normalized_answer":{"secret":"student answer"} if answered else None,"methodology":method}
    return {"snapshot_schema_version":"checking_input_v1","handoff_version":1,
            "routing_contract_version":"checking_routing_contract_v1","items":[item]}


def exact(s):
    s["items"][0]["methodology"]["accepted_answers"]=[answer("text",canonical_text="X",
        normalization_policy_code="exact_text_v1",normalization_policy_version=1)]


def numeric(s):
    s["items"][0]["methodology"]["accepted_answers"]=[answer("decimal",canonical_decimal="1.25",
        absolute_tolerance="0.1",relative_tolerance="0",normalization_policy_code="decimal_v1",normalization_policy_version=1)]


def expression(s):
    s["items"][0]["methodology"]["accepted_answers"]=[answer("expression",canonical_text="x+1",
        normalization_policy_code="expression_identity_v1",normalization_policy_version=1)]


def choice(s):
    option={"id":uid(),"option_key":"a","order_index":0,"label":"A"}
    s["items"][0]["methodology"].update(choice_options=[option],accepted_answers=[answer("choice_set",option_ids=[option["id"]])],
        choice_scoring_policy={"mode":"all_or_nothing","policy_version":1,"option_rules":[]})


def open_rubric(s):
    s["items"][0]["methodology"].update(expected_solution={"id":uid(),"solution_text":"secret solution"},rubric=rubric())


@pytest.mark.parametrize("fmt,prepare,checker",[
    ("short_text",exact,CheckerType.EXACT),("number",numeric,CheckerType.NUMERIC),
    ("single_choice",choice,CheckerType.MULTIPLE_CHOICE),("multiple_choice",choice,CheckerType.MULTIPLE_CHOICE),
    ("expression",expression,CheckerType.STRUCTURED_EXPRESSION),("long_text",open_rubric,CheckerType.LLM_RUBRIC)])
def test_answered_happy_routing(fmt,prepare,checker):
    value=snapshot(fmt); prepare(value); decision=route_snapshot(value)[0]
    assert (decision.checker_type,decision.disposition,decision.execution_required)==(checker,RoutingDisposition.READY,True)


def test_exact_precedes_valid_rubric():
    value=snapshot(); exact(value); open_rubric(value)
    assert route_snapshot(value)[0].checker_type is CheckerType.EXACT


def test_short_text_rubric_fallback():
    value=snapshot(); open_rubric(value)
    assert route_snapshot(value)[0].checker_type is CheckerType.LLM_RUBRIC


def test_long_text_never_exact():
    value=snapshot("long_text"); open_rubric(value); exact(value)
    decision=route_snapshot(value)[0]
    assert decision.checker_type is CheckerType.MANUAL_REQUIRED
    assert decision.disposition is RoutingDisposition.INSUFFICIENT_RUBRIC


@pytest.mark.parametrize("fmt,checker",[("short_text",CheckerType.EXACT),("number",CheckerType.NUMERIC),
 ("single_choice",CheckerType.MULTIPLE_CHOICE),("multiple_choice",CheckerType.MULTIPLE_CHOICE),
 ("expression",CheckerType.STRUCTURED_EXPRESSION),("long_text",CheckerType.LLM_RUBRIC)])
def test_unanswered_fast_path(fmt,checker):
    decision=route_snapshot(snapshot(fmt,answered=False))[0]
    assert (decision.checker_type,decision.disposition,decision.execution_required)==(checker,RoutingDisposition.UNANSWERED,False)


@pytest.mark.parametrize("mutation,reason,disposition",[
 (lambda s:s["items"][0].update(normalized_answer=None),RoutingReason.MALFORMED_SNAPSHOT,RoutingDisposition.INSUFFICIENT_RUBRIC),
 (lambda s:s["items"][0]["methodology"].update(answer_format="number"),RoutingReason.ANSWER_FORMAT_MISMATCH,RoutingDisposition.INSUFFICIENT_RUBRIC),
 (lambda s:s["items"][0]["methodology"].update(task_type="test"),RoutingReason.INCOMPATIBLE_TASK_FORMAT,RoutingDisposition.INSUFFICIENT_RUBRIC),
])
def test_item_shape_failures(mutation,reason,disposition):
    value=snapshot(); exact(value); mutation(value); decision=route_snapshot(value)[0]
    assert (decision.reason_code,decision.disposition)==(reason,disposition)


def test_unknown_future_format_is_manual():
    decision=route_snapshot(snapshot("future_format"))[0]
    assert (decision.checker_type,decision.reason_code)==(CheckerType.MANUAL_REQUIRED,RoutingReason.UNKNOWN_ANSWER_FORMAT)


@pytest.mark.parametrize("mutation,reason",[
 (lambda s:s["items"][0]["methodology"].update(accepted_answers=[answer("legacy_untyped")]),RoutingReason.LEGACY_UNTYPED_ANSWER),
 (lambda s:s["items"][0]["methodology"].update(accepted_answers=[answer("text",canonical_text="X",normalization_policy_code="exact_text_v1",normalization_policy_version=1),answer("legacy_untyped")]),RoutingReason.LEGACY_UNTYPED_ANSWER),
 (lambda s:None,RoutingReason.MISSING_TYPED_ANSWER),
 (lambda s:s["items"][0]["methodology"].update(accepted_answers=[answer("decimal")]),RoutingReason.INCOMPATIBLE_ANSWER_KIND),
 (lambda s:s["items"][0]["methodology"].update(accepted_answers=[answer("text",canonical_text="",normalization_policy_code="exact_text_v1",normalization_policy_version=1)]),RoutingReason.MISSING_CANONICAL_VALUE),
 (lambda s:s["items"][0]["methodology"].update(accepted_answers=[answer("text",canonical_text="X",normalization_policy_code="exact_text_v1",normalization_policy_version=1),answer("text",canonical_text="X",normalization_policy_code="exact_text_v1",normalization_policy_version=1)]),RoutingReason.DUPLICATE_CANONICAL),
])
def test_exact_methodology_failures(mutation,reason):
    value=snapshot(); mutation(value); assert route_snapshot(value)[0].reason_code is reason


@pytest.mark.parametrize("field,value",[("absolute_tolerance",None),("absolute_tolerance","-1"),("relative_tolerance","NaN")])
def test_invalid_numeric_tolerances(field,value):
    item=snapshot("number"); numeric(item); item["items"][0]["methodology"]["accepted_answers"][0][field]=value
    assert route_snapshot(item)[0].reason_code is RoutingReason.INVALID_NUMERIC_TOLERANCE


def test_unsupported_unit_is_manual():
    value=snapshot("number"); numeric(value); value["items"][0]["methodology"]["accepted_answers"][0]["unit_code"]="m"
    assert route_snapshot(value)[0].reason_code is RoutingReason.UNSUPPORTED_UNIT


@pytest.mark.parametrize("mutation,reason",[
 (lambda m:m.update(choice_options=[]),RoutingReason.MISSING_CHOICE_OPTIONS),
 (lambda m:m["choice_options"].append(dict(m["choice_options"][0])),RoutingReason.DUPLICATE_CHOICE_OPTION),
 (lambda m:m["accepted_answers"][0].update(option_ids=[uid()]),RoutingReason.UNKNOWN_CHOICE_OPTION),
 (lambda m:m.update(choice_scoring_policy=None),RoutingReason.MISSING_CHOICE_POLICY),
 (lambda m:m["choice_scoring_policy"].update(mode="per_option"),RoutingReason.INVALID_WEIGHTED_POLICY),
])
def test_choice_failures(mutation,reason):
    value=snapshot("single_choice"); choice(value); mutation(value["items"][0]["methodology"])
    assert route_snapshot(value)[0].reason_code is reason


def test_single_choice_cardinality():
    value=snapshot("single_choice"); choice(value); method=value["items"][0]["methodology"]
    second={"id":uid(),"option_key":"b","order_index":1}; method["choice_options"].append(second)
    method["accepted_answers"][0]["option_ids"].append(second["id"])
    assert route_snapshot(value)[0].reason_code is RoutingReason.INVALID_SINGLE_CHOICE


@pytest.mark.parametrize("policy,reason",[(None,RoutingReason.EXPRESSION_EQUIVALENCE),("algebraic_v1",RoutingReason.EXPRESSION_EQUIVALENCE)])
def test_expression_policy_boundary(policy,reason):
    value=snapshot("expression"); expression(value); value["items"][0]["methodology"]["accepted_answers"][0]["normalization_policy_code"]=policy
    assert route_snapshot(value)[0].reason_code is reason


@pytest.mark.parametrize("mutation,reason,disposition",[
 (lambda m:m.update(expected_solution=None),RoutingReason.MISSING_EXPECTED_SOLUTION,RoutingDisposition.INSUFFICIENT_RUBRIC),
 (lambda m:m.update(rubric=None),RoutingReason.MISSING_RUBRIC,RoutingDisposition.INSUFFICIENT_RUBRIC),
 (lambda m:m["rubric"].update(max_score="3"),RoutingReason.RUBRIC_SCORE_MISMATCH,RoutingDisposition.INSUFFICIENT_RUBRIC),
 (lambda m:m["rubric"].update(grading_mode="levels"),RoutingReason.UNSUPPORTED_GRADING_MODE,RoutingDisposition.MANUAL_REQUIRED),
])
def test_rubric_failures(mutation,reason,disposition):
    value=snapshot("long_text"); open_rubric(value); mutation(value["items"][0]["methodology"]); decision=route_snapshot(value)[0]
    assert (decision.reason_code,decision.disposition)==(reason,disposition)


def test_future_contract_versions_are_manual():
    for field,value in [("snapshot_schema_version","checking_input_v2"),("routing_contract_version","v2"),("handoff_version",2)]:
        item=snapshot(); item[field]=value
        assert route_snapshot(item)[0].reason_code is RoutingReason.UNSUPPORTED_CONTRACT_VERSION


@pytest.mark.parametrize("mutation",[lambda s:s.update(snapshot_schema_version=1),lambda s:s.update(items={}),lambda s:s["items"].append(s["items"][0])])
def test_outer_malformed_contract_raises_typed_privacy_safe_error(mutation):
    value=snapshot(); mutation(value)
    with pytest.raises(RoutingInputError) as error: route_snapshot(value)
    assert "student answer" not in str(error.value)


def test_deterministic_order_privacy_and_non_mutation():
    value=snapshot(); exact(value); second=copy.deepcopy(value["items"][0]); second["assessment_item_id"]=uid(); second["task_version_id"]=uid(); value["items"].append(second)
    before=copy.deepcopy(value); first=route_snapshot(value); again=route_snapshot(value)
    assert first==again and value==before and [x.assessment_item_id for x in first]==[x["assessment_item_id"] for x in value["items"]]
    serialized=json.dumps([asdict(x) for x in first],default=lambda x:x.value)
    for secret in ("student answer","secret statement","X","secret solution","student_id","participant_id"): assert secret not in serialized


def test_canonical_phase_42_fixture_routes_unanswered_exact():
    value=json.loads(CANONICAL_INPUT_FIXTURE.read_text(encoding="utf-8"))
    decision=route_snapshot(value)[0]
    assert (decision.checker_type,decision.disposition,decision.execution_required)==(CheckerType.EXACT,RoutingDisposition.UNANSWERED,False)


def test_common_async_checker_protocol():
    class Fake:
        checker_type=CheckerType.EXACT; checker_version="fake_v1"
        async def check(self,request): return CheckerResultDraft(
            assessment_item_id=request.decision.assessment_item_id,
            task_version_id=request.decision.task_version_id,
            outcome=CheckerOutcome.CORRECT,checker_type=self.checker_type,
            checker_version=self.checker_version,reason_code=ResultReason.EXACT_MATCH,
            score_suggested=Decimal("2.00"),max_score=Decimal("2.00"),
            confidence=Decimal("1.0000"),summary="fake result",
            student_feedback_draft=None,teacher_summary=None,
            needs_human_review=False,needs_human_review_reason=None)
    fake=Fake(); assert isinstance(fake,Checker)
    value=snapshot(answered=False); decision=route_snapshot(value)[0]
    assert asyncio.run(fake.check(CheckerRequest(value["items"][0],decision))).outcome is CheckerOutcome.CORRECT
