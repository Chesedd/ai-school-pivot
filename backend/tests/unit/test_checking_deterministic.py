import asyncio
import copy
import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.checking_deterministic import (
    ChoiceChecker, DeterministicExecutionError, ExactChecker, execute_deterministic,
    result_to_json_safe, serialize_result,
)
from app.application.checking_routing import (
    CheckerOutcome, CheckerRequest, ResultContractError, ResultReason,
    RoutingDisposition, route_snapshot,
)


def uid(): return str(uuid4())


def item(fmt="short_text", answer=None, points="2.50"):
    task_type="test" if "choice" in fmt else "essay" if fmt=="long_text" else "calculation"
    return {"assessment_item_id":uid(),"task_version_id":uid(),"points":points,
        "answer_format":fmt,"raw_answer":answer,"normalized_answer":answer,
        "methodology":{"statement":"PRIVATE STATEMENT","task_type":task_type,"answer_format":fmt,
            "accepted_answers":[],"choice_options":[],"choice_scoring_policy":None,
            "expected_solution":None,"rubric":None}}


def exact_item(actual="Alpha", alternatives=("Alpha",)):
    value=item("short_text",{"text":actual}); value["raw_answer"]="PRIVATE RAW"
    value["methodology"]["accepted_answers"]=[{"id":uid(),"value_kind":"text",
        "canonical_text":text,"normalization_policy_code":"exact_text_v1",
        "normalization_policy_version":1} for text in alternatives]
    return value


def choice_item(fmt="multiple_choice", selected=None, mode="all_or_nothing", alternatives=None):
    ids=[uid(),uid(),uid()]; normalized={"option_id":selected or ids[0]} if fmt=="single_choice" else {"option_ids":selected if selected is not None else [ids[0],ids[1]]}
    value=item(fmt,normalized); value["raw_answer"]="PRIVATE RAW"
    value["methodology"]["choice_options"]=[{"id":oid,"option_key":chr(97+i),"order_index":i,"label":"PRIVATE LABEL"} for i,oid in enumerate(ids)]
    sets=alternatives or [[ids[0]] if fmt=="single_choice" else [ids[0],ids[1]]]
    value["methodology"]["accepted_answers"]=[{"id":uid(),"value_kind":"choice_set","option_ids":chosen} for chosen in sets]
    rules=[]
    if mode=="per_option":
        rules=[{"option_id":ids[0],"option_key":"a","role":"correct","weight":"0.500000"},
               {"option_id":ids[1],"option_key":"b","role":"correct","weight":"0.500000"},
               {"option_id":ids[2],"option_key":"c","role":"distractor","weight":"-0.250000"}]
    value["methodology"]["choice_scoring_policy"]={"mode":mode,"policy_version":1,"option_rules":rules}
    return value,ids


def request(value):
    snapshot={"snapshot_schema_version":"checking_input_v1","handoff_version":1,
        "routing_contract_version":"checking_routing_contract_v1","items":[value]}
    return CheckerRequest(value,route_snapshot(snapshot)[0])


def run(value, registry=None): return asyncio.run(execute_deterministic(request(value),registry))


@pytest.mark.parametrize("actual,accepted,outcome,score",[
    ("Alpha",("Alpha",),CheckerOutcome.CORRECT,Decimal("2.50")),
    ("Beta",("Alpha","Beta"),CheckerOutcome.CORRECT,Decimal("2.50")),
    ("alpha",("Alpha",),CheckerOutcome.INCORRECT,Decimal("0.00")),
    ("A  B",("A B",),CheckerOutcome.INCORRECT,Decimal("0.00")),
    ("",("Alpha",),CheckerOutcome.INCORRECT,Decimal("0.00")),
])
def test_exact_equality_only(actual,accepted,outcome,score):
    result=run(exact_item(actual,accepted)); assert (result.outcome,result.score_suggested)==(outcome,score)


@pytest.mark.parametrize("normalized",[{}, {"text":1}, {"text":"Alpha","extra":1}, {"option_id":uid()}])
def test_exact_malformed_student_is_unclear(normalized):
    value=exact_item(); value["normalized_answer"]=normalized
    result=asyncio.run(ExactChecker().check(request(value)))
    assert result.outcome is CheckerOutcome.UNCLEAR and result.needs_human_review


def test_exact_invalid_methodology_fails_closed_and_does_not_leak():
    value=exact_item("secret actual",("secret expected","secret expected"))
    # Construct the READY decision before corrupting the immutable execution copy.
    req=request(exact_item()); req=CheckerRequest(value,req.decision.__class__(
        value["assessment_item_id"],value["task_version_id"],req.decision.routing_contract_version,
        req.decision.checker_type,req.decision.candidate_checker_type,RoutingDisposition.READY,
        req.decision.reason_code,False,True))
    result=asyncio.run(ExactChecker().check(req)); serialized=serialize_result(result)
    assert result.outcome is CheckerOutcome.INSUFFICIENT_RUBRIC
    assert "secret" not in serialized and "PRIVATE" not in serialized


def test_exact_is_deterministic_and_does_not_mutate_inputs():
    value=exact_item(); before=copy.deepcopy(value); req=request(value); decision=copy.deepcopy(req.decision)
    first=asyncio.run(ExactChecker().check(req)); second=asyncio.run(ExactChecker().check(req))
    assert serialize_result(first)==serialize_result(second) and value==before and req.decision==decision


def test_single_choice_correct_wrong_and_unknown():
    value,ids=choice_item("single_choice"); assert run(value).outcome is CheckerOutcome.CORRECT
    value["normalized_answer"]={"option_id":ids[1]}; assert run(value).outcome is CheckerOutcome.INCORRECT
    value["normalized_answer"]={"option_id":uid()}; result=run(value)
    assert (result.outcome,result.reason_code)==(CheckerOutcome.UNCLEAR,ResultReason.UNKNOWN_CHOICE_OPTION)


def test_multiple_choice_order_and_or_alternatives_are_deterministic():
    value,ids=choice_item(); value["methodology"]["accepted_answers"].append({"id":uid(),"value_kind":"choice_set","option_ids":[ids[2]]})
    first=run(value); value["normalized_answer"]={"option_ids":[ids[1],ids[0]]}; second=run(value)
    assert first.outcome is CheckerOutcome.CORRECT and serialize_result(first)==serialize_result(second)
    value["normalized_answer"]={"option_ids":[ids[2]]}; assert run(value).outcome is CheckerOutcome.CORRECT


@pytest.mark.parametrize("mutation",[
    lambda v,ids:v.update(normalized_answer={"option_ids":[ids[0],ids[0]]}),
    lambda v,ids:v.update(normalized_answer={"option_id":ids[0]}),
    lambda v,ids:v.update(normalized_answer={"option_ids":["B"]}),
])
def test_multiple_choice_malformed_or_unknown(mutation):
    value,ids=choice_item(); mutation(value,ids)
    # Unknown non-UUID is malformed; labels/keys are never identity.
    assert run(value).outcome is CheckerOutcome.UNCLEAR


@pytest.mark.parametrize("selected,expected,score",[
    ((0,),CheckerOutcome.PARTIALLY_CORRECT,Decimal("1.25")),
    ((0,2),CheckerOutcome.PARTIALLY_CORRECT,Decimal("0.63")),
    ((2,),CheckerOutcome.INCORRECT,Decimal("0.00")),
    ((0,1),CheckerOutcome.CORRECT,Decimal("2.50")),
])
def test_per_option_decimal_scoring(selected,expected,score):
    value,ids=choice_item(mode="per_option"); value["normalized_answer"]={"option_ids":[ids[x] for x in selected]}
    result=run(value); assert (result.outcome,result.score_suggested)==(expected,score)


def test_invalid_weighted_policy_routes_fallback_without_checker_call():
    value,ids=choice_item(mode="per_option"); value["methodology"]["choice_scoring_policy"]["option_rules"][0]["weight"]="0.4"
    calls=[]
    class Spy:
        checker_type=request(choice_item()[0]).decision.checker_type; checker_version="spy"
        async def check(self,request): calls.append(1); raise AssertionError
    result=run(value,{Spy.checker_type:Spy()})
    assert result.outcome is CheckerOutcome.INSUFFICIENT_RUBRIC and calls==[]


def test_unanswered_all_natural_types_bypass_registry():
    class Spy:
        checker_type=None; checker_version="spy"
        async def check(self,request): raise AssertionError
    for fmt in ("short_text","single_choice","multiple_choice","number","expression","long_text"):
        value=item(fmt,None); result=run(value,{})
        assert result.outcome is CheckerOutcome.INCORRECT and result.score_suggested==Decimal("0.00")


def test_result_json_decimal_immutability_and_privacy():
    result=run(exact_item()); safe=result_to_json_safe(result)
    assert safe["max_score"]=="2.50" and safe["confidence"]=="1.0000"
    assert not any(isinstance(x,float) for x in safe.values())
    json.dumps(safe,allow_nan=False)
    with pytest.raises(FrozenInstanceError): result.summary="changed"
    for secret in ("PRIVATE RAW","PRIVATE STATEMENT","Alpha","PRIVATE LABEL","student_id","assignment_id"):
        assert secret not in serialize_result(result)


def test_result_contract_rejects_silent_score_repair():
    result=run(exact_item())
    with pytest.raises(ResultContractError):
        result.__class__(**{**result.__dict__,"score_suggested":Decimal("1.00")})


def test_ready_unsupported_checker_is_typed_and_private():
    value=item("long_text",{"text":"SECRET"}); value["raw_answer"]="SECRET"
    value["methodology"]["expected_solution"]={"solution_text":"PRIVATE"}
    value["methodology"]["rubric"]={"grading_mode":"points","max_score":"2.5","items":[
        {"id":uid(),"order_index":0,"criterion":"PRIVATE","max_points":"2.5"}]}
    with pytest.raises(DeterministicExecutionError) as error: run(value)
    assert str(error.value)=="unsupported_checker_execution" and "SECRET" not in str(error.value)
