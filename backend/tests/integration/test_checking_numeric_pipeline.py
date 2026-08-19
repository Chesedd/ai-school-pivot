import asyncio
import copy
from uuid import uuid4

import pytest

from app.application.checking_deterministic import execute_deterministic, serialize_result
from app.application.checking_routing import CheckerOutcome, CheckerRequest, RoutingDisposition, route_snapshot
from app.application.student_assessments import normalize_answer


def uid(): return str(uuid4())


def pipeline(raw, expected="1", absolute="0", relative="0", alternatives=None):
    normalized=None if raw is None else normalize_answer("number",raw)
    answers=alternatives or [(expected,absolute,relative,None)]
    item={"assessment_item_id":uid(),"task_version_id":uid(),"position":1,"points":"3.00",
        "answer_format":"number","raw_answer":raw,"normalized_answer":normalized,
        "methodology":{"statement":"private","task_type":"calculation","answer_format":"number",
          "accepted_answers":[{"id":uid(),"value_kind":"decimal","canonical_decimal":e,
            "absolute_tolerance":a,"relative_tolerance":r,"unit_code":unit,
            "normalization_policy_code":"decimal_v1","normalization_policy_version":1} for e,a,r,unit in answers],
          "choice_options":[],"choice_scoring_policy":None,"expected_solution":None,"rubric":None}}
    snapshot={"snapshot_schema_version":"checking_input_v1","handoff_version":1,
      "routing_contract_version":"checking_routing_contract_v1","items":[item]}
    decision=route_snapshot(snapshot)[0]
    return snapshot,item,decision


@pytest.mark.parametrize("raw,expected,absolute,relative,outcome",[
    ("1e-40","0.0000000000000000000000000000000000000001","0","0",CheckerOutcome.CORRECT),
    ("1.1","1","0.1","0",CheckerOutcome.CORRECT),
    ("110","100","0","0.1",CheckerOutcome.CORRECT),
    ("1","1",None,None,CheckerOutcome.CORRECT),
    ("1.00000000000000000000000000001","1","0.00000000000000000000000000001","0",CheckerOutcome.CORRECT),
])
def test_real_normalization_routing_execution_pipeline(raw,expected,absolute,relative,outcome):
    snapshot,item,decision=pipeline(raw,expected,absolute,relative); before=copy.deepcopy(snapshot)
    result=asyncio.run(execute_deterministic(CheckerRequest(item,decision)))
    assert result.outcome is outcome and snapshot == before
    assert serialize_result(result) == serialize_result(asyncio.run(execute_deterministic(CheckerRequest(item,decision))))


def test_or_alternatives():
    _,item,decision=pipeline("2",alternatives=[("1","0","0",None),("2","0","0",None)])
    assert asyncio.run(execute_deterministic(CheckerRequest(item,decision))).outcome is CheckerOutcome.CORRECT


def test_historical_malformed_is_unclear():
    _,item,decision=pipeline("1"); item["normalized_answer"]={"decimal":"1e0"}
    assert asyncio.run(execute_deterministic(CheckerRequest(item,decision))).outcome is CheckerOutcome.UNCLEAR


@pytest.mark.parametrize("raw,alternatives,disposition",[("1",[("1","0","0","m")],RoutingDisposition.MANUAL_REQUIRED),
    (None,[("1","0","0",None)],RoutingDisposition.UNANSWERED)])
def test_fallbacks_bypass_numeric_checker(raw,alternatives,disposition):
    _,item,decision=pipeline(raw,alternatives=alternatives); calls=[]
    class Spy:
        checker_type=decision.candidate_checker_type; checker_version="spy"
        async def check(self,request): calls.append(1); raise AssertionError
    result=asyncio.run(execute_deterministic(CheckerRequest(item,decision),{Spy.checker_type:Spy()}))
    assert decision.disposition is disposition and calls == []
    assert result.outcome in {CheckerOutcome.MANUAL_REQUIRED,CheckerOutcome.INCORRECT}
