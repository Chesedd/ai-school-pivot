import asyncio
import copy
from uuid import uuid4

import pytest

from app.application.checking_deterministic import execute_deterministic, serialize_result
from app.application.checking_routing import CheckerOutcome, CheckerRequest, ResultReason, route_snapshot
from app.application.student_assessments import normalize_answer


def uid(): return str(uuid4())

def pipeline(raw="x+1", accepted=("x+1",), mutation=None):
    normalized=None if raw is None else normalize_answer("expression",raw)
    item={"assessment_item_id":uid(),"task_version_id":uid(),"points":"4.00","position":0,
      "answer_format":"expression","raw_answer":raw,"normalized_answer":normalized,
      "methodology":{"statement":"PRIVATE_MARKER","task_type":"calculation","answer_format":"expression",
      "accepted_answers":[{"id":uid(),"value_kind":"expression","canonical_text":x,
      "normalization_policy_code":"expression_identity_v1","normalization_policy_version":1} for x in accepted],
      "choice_options":[],"choice_scoring_policy":None,"expected_solution":None,"rubric":None}}
    if mutation: mutation(item)
    snapshot={"snapshot_schema_version":"checking_input_v1","handoff_version":1,
      "routing_contract_version":"checking_routing_contract_v1","items":[item]}
    before=copy.deepcopy(snapshot); decision=route_snapshot(snapshot)[0]
    result=asyncio.run(execute_deterministic(CheckerRequest(item,decision)))
    assert snapshot==before
    return decision,result


def test_real_chain_identity_or_and_determinism_privacy():
    _,result=pipeline("y",("x","y")); assert result.outcome is CheckerOutcome.CORRECT
    _,again=pipeline("x+1",("x+1",)); serialized=serialize_result(again)
    assert serialize_result(again)==serialize_result(again)
    assert "PRIVATE_MARKER" not in serialized and "x+1" not in serialized

@pytest.mark.parametrize("raw",["x + 1","1+x"])
def test_real_chain_nonidentical_manual(raw):
    _,result=pipeline(raw); assert result.outcome is CheckerOutcome.MANUAL_REQUIRED and result.score_suggested is None


def test_historical_malformed_and_methodology_invalid():
    _,result=pipeline(mutation=lambda i:i.update(normalized_answer={"expression":1}))
    assert result.outcome is CheckerOutcome.UNCLEAR
    _,result=pipeline(mutation=lambda i:i["methodology"]["accepted_answers"][0].update(canonical_text=""))
    assert result.outcome is CheckerOutcome.INSUFFICIENT_RUBRIC


def test_unsupported_and_unanswered_bypass_execution():
    decision,result=pipeline(mutation=lambda i:i["methodology"]["accepted_answers"][0].update(normalization_policy_version=2))
    assert not decision.execution_required and result.outcome is CheckerOutcome.MANUAL_REQUIRED
    decision,result=pipeline(None)
    assert not decision.execution_required and result.reason_code is ResultReason.UNANSWERED


def test_malicious_expression_is_only_data(tmp_path):
    target=tmp_path/"pwned"; text=f'__import__("pathlib").Path("{target}").touch()'
    _,result=pipeline(text,(text,)); assert result.outcome is CheckerOutcome.CORRECT and not target.exists()
