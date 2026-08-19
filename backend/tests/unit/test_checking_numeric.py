import asyncio
import copy
import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.checking_deterministic import NumericChecker, execute_deterministic, serialize_result
from app.application.checking_routing import (Checker, CheckerOutcome, CheckerRequest,
    ResultReason, RoutingDisposition, route_snapshot)


def uid(): return str(uuid4())


def item(actual="1", alternatives=None):
    answers = alternatives or [(uid(), "1", "0", "0")]
    return {"assessment_item_id":uid(), "task_version_id":uid(), "points":"2.50",
        "answer_format":"number", "raw_answer":"PRIVATE RAW", "normalized_answer":{"decimal":actual},
        "methodology":{"statement":"PRIVATE STATEMENT", "task_type":"calculation", "answer_format":"number",
            "accepted_answers":[{"id":identifier,"value_kind":"decimal","canonical_decimal":expected,
                "absolute_tolerance":absolute,"relative_tolerance":relative,"unit_code":None,
                "normalization_policy_code":"decimal_v1","normalization_policy_version":1}
                for identifier,expected,absolute,relative in answers], "choice_options":[],
            "choice_scoring_policy":None,"expected_solution":None,"rubric":None}}


def request(value):
    snapshot={"snapshot_schema_version":"checking_input_v1","handoff_version":1,
        "routing_contract_version":"checking_routing_contract_v1","items":[value]}
    return CheckerRequest(value,route_snapshot(snapshot)[0])


def run(value): return asyncio.run(execute_deterministic(request(value)))


@pytest.mark.parametrize("actual,expected,absolute,relative,outcome", [
    ("1","1","0","0",CheckerOutcome.CORRECT),
    ("1","1",None,None,CheckerOutcome.CORRECT),
    ("1.1","1","0.1","0",CheckerOutcome.CORRECT),
    ("1.1000000000000000000000000001","1","0.1","0",CheckerOutcome.INCORRECT),
    ("110","100","0","0.1",CheckerOutcome.CORRECT),
    ("112","100","2","0.1",CheckerOutcome.CORRECT),
    ("-110","-100","0","0.1",CheckerOutcome.CORRECT),
    ("0.0001","0","0","1",CheckerOutcome.INCORRECT),
    ("0.0000000000000000000000000001","0.0000000000000000000000000002","0.0000000000000000000000000001","0",CheckerOutcome.CORRECT),
    ("123456789012345678901234567890.1","123456789012345678901234567890","0.1","0",CheckerOutcome.CORRECT),
    ("999999999999999999999999999999999999","1000000000000000000000000000000000000","1","0",CheckerOutcome.CORRECT),
])
def test_decimal_comparison_semantics(actual,expected,absolute,relative,outcome):
    result=run(item(actual,[(uid(),expected,absolute,relative)]))
    assert result.outcome is outcome
    assert result.score_suggested == (Decimal("2.50") if outcome is CheckerOutcome.CORRECT else Decimal("0.00"))
    assert result.confidence == Decimal("1.0000") and result.checker_version == "numeric_v1"


def test_or_alternative_specific_tolerances_and_deterministic_selection():
    close=uid(); match=uid(); answers=[(close,"8","0","0"),(match,"10","1","0")]
    result=run(item("10.5",answers))
    assert result.outcome is CheckerOutcome.CORRECT
    assert result.evidence["matched_accepted_answer_id"] == match
    assert result.evidence["alternatives_checked"] == 2


def test_multiple_matches_choose_delta_then_uuid_and_ignore_input_order():
    low="00000000-0000-4000-8000-000000000001"; high="00000000-0000-4000-8000-000000000002"
    answers=[(high,"11","2","0"),(low,"9","2","0")]
    value=item("10",answers); reversed_value=copy.deepcopy(value)
    reversed_value["methodology"]["accepted_answers"].reverse()
    first=run(value); second=run(reversed_value)
    assert first.evidence["matched_accepted_answer_id"] == low
    assert serialize_result(first) == serialize_result(second)


def test_mismatch_selects_minimum_excess_then_delta_then_uuid():
    low="00000000-0000-4000-8000-000000000001"; high="00000000-0000-4000-8000-000000000002"
    result=run(item("10",[(high,"12","1","0"),(low,"8","1","0")]))
    assert result.evidence["compared_accepted_answer_id"] == low


@pytest.mark.parametrize("normalized", [{}, {"decimal":"1","extra":1},{"decimal":1},{"decimal":1.0},
    {"decimal":"NaN"},{"decimal":"Infinity"},{"decimal":"1e0"},{"decimal":"-0"},
    {"decimal":"+1"},{"decimal":" 1"},{"decimal":"1.0"}])
def test_malformed_historical_normalized_answer_is_unclear(normalized):
    value=item(); value["normalized_answer"]=normalized
    # Preserve a genuine READY decision while injecting historical corruption.
    req=request(item()); req=CheckerRequest(value,req.decision.__class__(value["assessment_item_id"],value["task_version_id"],
        req.decision.routing_contract_version,req.decision.checker_type,req.decision.candidate_checker_type,
        RoutingDisposition.READY,req.decision.reason_code,False,True))
    result=asyncio.run(NumericChecker().check(req))
    assert (result.outcome,result.reason_code,result.score_suggested,result.needs_human_review)==(
        CheckerOutcome.UNCLEAR,ResultReason.MALFORMED_NORMALIZED_ANSWER,None,True)


@pytest.mark.parametrize("mutation", [
    lambda a:a.update(absolute_tolerance="-1"), lambda a:a.update(relative_tolerance="NaN"),
    lambda a:a.update(canonical_decimal="1e0"), lambda a:a.update(normalization_policy_version=2),
    lambda a:a.update(unit_code="m"), lambda a:a.update(value_kind="legacy_untyped")])
def test_forced_ready_invalid_methodology_fails_closed(mutation):
    value=item(); mutation(value["methodology"]["accepted_answers"][0]); good=request(item())
    decision=good.decision.__class__(value["assessment_item_id"],value["task_version_id"],good.decision.routing_contract_version,
        good.decision.checker_type,good.decision.candidate_checker_type,RoutingDisposition.READY,good.decision.reason_code,False,True)
    result=asyncio.run(NumericChecker().check(CheckerRequest(value,decision)))
    assert (result.outcome,result.reason_code)==(CheckerOutcome.INSUFFICIENT_RUBRIC,ResultReason.INVALID_NUMERIC_METHODOLOGY)


def test_duplicate_decimal_values_and_ids_are_invalid():
    identifier=uid()
    for answers in [[(uid(),"1","0","0"),(uid(),"1","1","0")],
                    [(identifier,"1","0","0"),(identifier,"2","0","0")]]:
        value=item("1",answers); req=request(item()); decision=req.decision.__class__(value["assessment_item_id"],value["task_version_id"],
            req.decision.routing_contract_version,req.decision.checker_type,req.decision.candidate_checker_type,
            RoutingDisposition.READY,req.decision.reason_code,False,True)
        assert asyncio.run(NumericChecker().check(CheckerRequest(value,decision))).outcome is CheckerOutcome.INSUFFICIENT_RUBRIC


def test_json_evidence_is_plain_float_free_private_immutable_and_input_unchanged():
    value=item("1.1",[(uid(),"1","0.1","0")]); before=copy.deepcopy(value); result=run(value)
    serialized=serialize_result(result); safe=json.loads(serialized)
    assert value == before and "e" not in safe["evidence"]["delta"].lower()
    assert not any(isinstance(x,float) for x in safe["evidence"].values())
    for secret in ("PRIVATE", '"canonical_decimal"', '"statement"'): assert secret not in serialized
    with pytest.raises(TypeError): result.evidence["delta"]="secret"
    with pytest.raises(FrozenInstanceError): result.summary="secret"
    assert isinstance(NumericChecker(),Checker)
