"""Database-free proof of normalization -> frozen routing -> deterministic checking."""
import asyncio
import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.application.checking_deterministic import execute_deterministic, serialize_result
from app.application.checking_routing import CheckerOutcome, CheckerRequest, route_snapshot
from app.application.student_assessments import normalize_answer

FIXTURE=Path(__file__).resolve().parents[1]/"fixtures"/"checking_input_v1_canonical.json"


def uid(): return str(uuid4())


def snapshot(fmt,raw,accepted,*,options=(),policy=None,points="3.00"):
    normalized=normalize_answer(fmt,raw)
    task="test" if "choice" in fmt else "calculation"
    item={"assessment_item_id":uid(),"task_version_id":uid(),"points":points,"position":1,
        "answer_format":fmt,"raw_answer":raw,"normalized_answer":normalized,
        "methodology":{"statement":"DO NOT LEAK","task_type":task,"answer_format":fmt,
            "accepted_answers":accepted,"choice_options":list(options),"choice_scoring_policy":policy,
            "expected_solution":None,"rubric":None}}
    return {"snapshot_schema_version":"checking_input_v1","handoff_version":1,
        "routing_contract_version":"checking_routing_contract_v1","items":[item]}


async def execute(value):
    decision=route_snapshot(value)[0]
    return await execute_deterministic(CheckerRequest(value["items"][0],decision))


def exact_answer(text): return {"id":uid(),"value_kind":"text","canonical_text":text,
    "normalization_policy_code":"exact_text_v1","normalization_policy_version":1}


@pytest.mark.parametrize("raw,expected,outcome",[("  Café  ","Café",CheckerOutcome.CORRECT),("café","Café",CheckerOutcome.INCORRECT)])
def test_normalize_route_exact_execute(raw,expected,outcome):
    value=snapshot("short_text",raw,[exact_answer(expected)]); before=copy.deepcopy(value)
    first=asyncio.run(execute(value)); second=asyncio.run(execute(value))
    assert first.outcome is outcome and serialize_result(first)==serialize_result(second) and value==before
    assert "Café" not in serialize_result(first) and "DO NOT LEAK" not in serialize_result(first)


def choice_snapshot(fmt,raw,mode="all_or_nothing"):
    ids=[uid(),uid(),uid()]; options=[{"id":x,"option_key":chr(97+i),"order_index":i,"label":"SECRET"} for i,x in enumerate(ids)]
    answers=[{"id":uid(),"value_kind":"choice_set","option_ids":[ids[0]] if fmt=="single_choice" else [ids[0],ids[1]]}]
    if fmt=="multiple_choice" and mode=="all_or_nothing": answers.append({"id":uid(),"value_kind":"choice_set","option_ids":[ids[2]]})
    rules=[] if mode=="all_or_nothing" else [
        {"option_id":ids[0],"option_key":"a","role":"correct","weight":"0.500000"},
        {"option_id":ids[1],"option_key":"b","role":"correct","weight":"0.500000"},
        {"option_id":ids[2],"option_key":"c","role":"correct","weight":"0.000001"}]
    # Per-option accepted membership must agree with roles, so use a distractor and exact weight sum.
    if mode=="per_option": rules[-1].update(role="distractor",weight="-0.250000")
    policy={"mode":mode,"policy_version":1,"option_rules":rules}
    return ids,lambda selected:snapshot(fmt,selected,answers,options=options,policy=policy)


def test_single_choice_known_correct_wrong_and_unknown():
    ids,make=choice_snapshot("single_choice",None)
    assert asyncio.run(execute(make(ids[0]))).outcome is CheckerOutcome.CORRECT
    assert asyncio.run(execute(make(ids[1]))).outcome is CheckerOutcome.INCORRECT
    assert asyncio.run(execute(make(uid()))).outcome is CheckerOutcome.UNCLEAR


def test_multiple_choice_permutation_alternative_and_mismatch():
    ids,make=choice_snapshot("multiple_choice",None)
    assert asyncio.run(execute(make([ids[1],ids[0]]))).outcome is CheckerOutcome.CORRECT
    assert asyncio.run(execute(make([ids[2]]))).outcome is CheckerOutcome.CORRECT
    assert asyncio.run(execute(make([ids[0]]))).outcome is CheckerOutcome.INCORRECT


def test_multiple_choice_per_option_partial_exact_decimal():
    ids,make=choice_snapshot("multiple_choice",None,"per_option")
    result=asyncio.run(execute(make([ids[0],ids[2]])))
    assert result.outcome is CheckerOutcome.PARTIALLY_CORRECT and result.score_suggested==Decimal("0.75")


def test_canonical_unanswered_fixture_bypasses_checker_and_preserves_bytes():
    before=FIXTURE.read_bytes(); value=json.loads(before); calls=[]
    class Spy:
        checker_type=route_snapshot(value)[0].checker_type; checker_version="spy"
        async def check(self,request): calls.append(1); raise AssertionError
    decision=route_snapshot(value)[0]
    result=asyncio.run(execute_deterministic(CheckerRequest(value["items"][0],decision),{Spy.checker_type:Spy()}))
    assert (result.outcome,result.score_suggested,result.max_score)==(CheckerOutcome.INCORRECT,Decimal("0.00"),Decimal("2.50"))
    assert calls==[] and FIXTURE.read_bytes()==before
    assert hashlib.sha256(before).hexdigest()=="1c5f8d4e8cca96d4f2ec470860ac1b91d91f3957544185506e4a6e8612befe89"
