import asyncio
import copy
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from app.application.checking_deterministic import (
    DeterministicExecutionError, ExpressionChecker, ExpressionEquivalenceAdapter,
    ExpressionProofResult, IdentityExpressionEquivalenceAdapter, execute_deterministic,
    serialize_result,
)
from app.application.checking_routing import (
    Checker, CheckerOutcome, CheckerRequest, CheckerType, ResultReason,
    RoutingDisposition, route_snapshot,
)


def uid(): return str(uuid4())


def expression_item(actual="x+1", alternatives=("x+1",)):
    return {"assessment_item_id":uid(),"task_version_id":uid(),"points":"3.00",
        "answer_format":"expression","raw_answer":"PRIVATE_ACTUAL","normalized_answer":{"expression":actual},
        "methodology":{"statement":"PRIVATE STATEMENT","task_type":"calculation","answer_format":"expression",
        "accepted_answers":[{"id":uid(),"value_kind":"expression","canonical_text":value,
        "normalization_policy_code":"expression_identity_v1","normalization_policy_version":1,
        "answer_value":"INERT LEGACY","normalization_rule":"INERT FREE FORM"} for value in alternatives],
        "choice_options":[],"choice_scoring_policy":None,"expected_solution":None,"rubric":None}}


def request(value):
    envelope={"snapshot_schema_version":"checking_input_v1","handoff_version":1,
        "routing_contract_version":"checking_routing_contract_v1","items":[value]}
    return CheckerRequest(value,route_snapshot(envelope)[0])


def forced(value):
    good=request(expression_item())
    d=good.decision
    return CheckerRequest(value,d.__class__(value["assessment_item_id"],value["task_version_id"],
        d.routing_contract_version,CheckerType.STRUCTURED_EXPRESSION,CheckerType.STRUCTURED_EXPRESSION,
        RoutingDisposition.READY,d.reason_code,False,True))


def run(value): return asyncio.run(execute_deterministic(request(value)))


def test_identity_or_alternative_full_score_and_private_evidence():
    value=expression_item("SECRET_EXPECTED_B",("SECRET_EXPECTED_A","SECRET_EXPECTED_B")); result=run(value)
    assert result.outcome is CheckerOutcome.CORRECT and str(result.score_suggested)=="3.00"
    assert result.reason_code is ResultReason.EXPRESSION_IDENTITY_MATCH
    assert result.evidence["matched_accepted_answer_id"]==value["methodology"]["accepted_answers"][1]["id"]
    serialized=serialize_result(result)
    for secret in ("SECRET_EXPECTED_A","SECRET_EXPECTED_B","PRIVATE","INERT","student_id","assignment_id"): assert secret not in serialized


@pytest.mark.parametrize("actual",["x + 1","X+1","1+x","x+x","(x+1)","x−1","x*x"])
def test_nonidentity_is_manual_never_incorrect(actual):
    result=run(expression_item(actual,("x+1",)))
    assert result.outcome is CheckerOutcome.MANUAL_REQUIRED
    assert result.score_suggested is None and result.needs_human_review
    assert result.reason_code is ResultReason.EXPRESSION_EQUIVALENCE_UNPROVEN
    assert result.needs_human_review_reason=="expression_equivalence_unproven"


@pytest.mark.parametrize("normalized",[{}, {"text":"x"},{"expression":1},{"expression":""},
    {"expression":"x","extra":1},{"expression":"x"*60001},{"expression":"\ud800"}])
def test_malformed_historical_expression_is_unclear_without_adapter(normalized):
    calls=[]
    class Spy:
        async def check(self,*args): calls.append(args); return ExpressionProofResult.PROVEN_EQUIVALENT
    value=expression_item(); value["normalized_answer"]=normalized
    result=asyncio.run(ExpressionChecker(Spy()).check(forced(value)))
    assert result.outcome is CheckerOutcome.UNCLEAR and result.score_suggested is None and calls==[]


@pytest.mark.parametrize("mutate",[
    lambda a:a.update(value_kind="text"), lambda a:a.pop("normalization_policy_code"),
    lambda a:a.update(normalization_policy_version=2), lambda a:a.update(id="not-uuid"),
    lambda a:a.update(canonical_text=""), lambda a:a.update(canonical_text="x"*60001),
])
def test_invalid_methodology_is_insufficient_without_adapter(mutate):
    calls=[]
    class Spy:
        async def check(self,*args): calls.append(args)
    value=expression_item(); mutate(value["methodology"]["accepted_answers"][0])
    result=asyncio.run(ExpressionChecker(Spy()).check(forced(value)))
    assert result.outcome is CheckerOutcome.INSUFFICIENT_RUBRIC
    assert result.reason_code is ResultReason.INVALID_EXPRESSION_METHODOLOGY and calls==[]


def test_duplicate_uuid_and_canonical_are_insufficient_forced_ready():
    for field in ("id","canonical_text"):
        value=expression_item("x",("x","y")); answers=value["methodology"]["accepted_answers"]
        answers[1][field]=answers[0][field]
        assert asyncio.run(ExpressionChecker().check(forced(value))).outcome is CheckerOutcome.INSUFFICIENT_RUBRIC


def test_order_independent_selection_nonmutation_immutability_and_serialization():
    value=expression_item(); duplicate=dict(value["methodology"]["accepted_answers"][0]); duplicate["id"]=uid()
    # Defensive checker selection remains stable even if historical corruption duplicates text.
    req=forced(value); checker=ExpressionChecker(); original=checker._methodology
    checker._methodology=lambda method:[(duplicate["id"],"x+1"),(method["accepted_answers"][0]["id"],"x+1")]
    before=copy.deepcopy(value); first=asyncio.run(checker.check(req)); second=asyncio.run(checker.check(req))
    assert first.evidence["matched_accepted_answer_id"]==min(duplicate["id"],value["methodology"]["accepted_answers"][0]["id"])
    assert serialize_result(first)==serialize_result(second) and value==before
    with pytest.raises(FrozenInstanceError): first.summary="changed"
    with pytest.raises(TypeError): first.evidence["new"]=True
    checker._methodology=original


def test_protocols_identity_policy_and_invalid_adapter_response_are_bounded():
    adapter=IdentityExpressionEquivalenceAdapter()
    assert isinstance(adapter,ExpressionEquivalenceAdapter) and isinstance(ExpressionChecker(),Checker)
    assert asyncio.run(adapter.check("x","x","expression_identity_v1",1)) is ExpressionProofResult.PROVEN_EQUIVALENT
    assert asyncio.run(adapter.check("x","x","future",2)) is ExpressionProofResult.UNPROVEN
    class Bad:
        async def check(self,*args): return "PRIVATE expression"
    with pytest.raises(DeterministicExecutionError) as error:
        asyncio.run(ExpressionChecker(Bad()).check(request(expression_item())))
    assert str(error.value)=="invalid_expression_adapter_response" and "PRIVATE" not in str(error.value)


def test_malicious_text_is_inert_and_unanswered_or_unsupported_policy_bypasses_adapter(tmp_path):
    marker=tmp_path/"owned"; malicious=f'__import__("pathlib").Path("{marker}").touch()'
    assert run(expression_item(malicious,(malicious,))).outcome is CheckerOutcome.CORRECT
    assert not marker.exists()
    class Spy:
        checker_type=CheckerType.STRUCTURED_EXPRESSION; checker_version="spy"
        async def check(self,request): raise AssertionError
    unanswered=expression_item(); unanswered["raw_answer"]=unanswered["normalized_answer"]=None
    assert asyncio.run(execute_deterministic(request(unanswered),{CheckerType.STRUCTURED_EXPRESSION:Spy()})).reason_code is ResultReason.UNANSWERED
    unsupported=expression_item(); unsupported["methodology"]["accepted_answers"][0]["normalization_policy_version"]=2
    result=asyncio.run(execute_deterministic(request(unsupported),{CheckerType.STRUCTURED_EXPRESSION:Spy()}))
    assert result.outcome is CheckerOutcome.MANUAL_REQUIRED
