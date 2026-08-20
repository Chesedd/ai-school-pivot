from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType

import pytest

from app.application.checking_results import *
from app.application.checking_routing import CheckerOutcome, CheckerResultDraft, CheckerType, ResultReason

I="00000000-0000-0000-0000-000000000001"; T="00000000-0000-0000-0000-000000000002"
R="00000000-0000-0000-0000-000000000003"; E="00000000-0000-0000-0000-000000000004"; S="00000000-0000-0000-0000-000000000005"

def item(): return {"assessment_item_id":I,"task_version_id":T,"points":"10.00","rubric_item_ids":[R],"typical_error_ids":[E],"skill_ids":[S],"methodology":{"rubric":{"items":[{"id":R,"criterion":"Bounded criterion","required":True}]},"typical_errors":[{"id":E,"code":"E1","title":"Error","severity":"critical","skill_id":S}],"skills":[{"id":S,"code":"SK","name":"Skill"}]}}
def policy(**kw): return ConfidenceGatePolicy("p1",kw.get("threshold",Decimal("0.5000")),kw.get("missing",Decimal("0.1000")),kw.get("limits",Decimal("0.1000")),kw.get("fraction",Decimal("0.1000")),kw.get("border",Decimal("0.1000")))
def draft(**kw):
    return CheckerResultDraft(I,T,kw.get("outcome",CheckerOutcome.CORRECT),kw.get("checker",CheckerType.EXACT),"v1",kw.get("reason",ResultReason.EXACT_MATCH),kw.get("score",Decimal("10.00")),Decimal("10.00"),kw.get("confidence",Decimal("0.2000")),"summary",None,None,kw.get("review",False),kw.get("review_reason"),kw.get("limitations",()),kw.get("evidence",MappingProxyType({})),kw.get("findings",()),kw.get("rubric",()))

@pytest.mark.parametrize("outcome,reason,score,expected,review",[(CheckerOutcome.CORRECT,ResultReason.EXACT_MATCH,Decimal("10.00"),"deterministic_proof",False),(CheckerOutcome.INCORRECT,ResultReason.EXACT_MISMATCH,Decimal("0.00"),"deterministic_proof",False),(CheckerOutcome.INCORRECT,ResultReason.UNANSWERED,Decimal("0.00"),"unanswered",False)])
def test_deterministic_confidence(outcome,reason,score,expected,review):
    got=prepare_result(item(),draft(outcome=outcome,reason=reason,score=score),policy()); assert got.confidence.effective==Decimal("1.0000"); assert got.confidence.reasons[0].value==expected; assert got.confidence.needs_human_review is review

@pytest.mark.parametrize("outcome,reason",[(CheckerOutcome.MANUAL_REQUIRED,ResultReason.ROUTING_MANUAL_REQUIRED),(CheckerOutcome.INSUFFICIENT_RUBRIC,ResultReason.ROUTING_INSUFFICIENT_RUBRIC),(CheckerOutcome.UNCLEAR,ResultReason.LLM_PROVIDER_FAILURE)])
def test_zero_confidence_review_outcomes(outcome,reason):
    got=prepare_result(item(),draft(outcome=outcome,reason=reason,score=None,checker=CheckerType.MANUAL_REQUIRED if outcome is CheckerOutcome.MANUAL_REQUIRED else CheckerType.LLM_RUBRIC,review=True,review_reason="review"),policy()); assert got.confidence.effective==0 and got.confidence.needs_human_review

def test_llm_penalties_floor_rounding_and_review():
    p=policy(missing=Decimal("0.3333"),limits=Decimal("0.3333"),border=Decimal("0.3333"),fraction=Decimal("0.2"))
    d=draft(outcome=CheckerOutcome.PARTIALLY_CORRECT,reason=ResultReason.LLM_RUBRIC_EVALUATED,score=Decimal("1.00"),checker=CheckerType.LLM_RUBRIC,confidence=Decimal("0.9000"),review=True,review_reason="llm",limitations=("bounded",),evidence=MappingProxyType({"confidence_policy_version":"p1","confidence_reason_codes":("base",)}),rubric=(MappingProxyType({"status":"partial","evidence":()}),))
    got=prepare_result(item(),d,p); assert got.confidence.effective==Decimal("0.0000"); assert len(got.confidence.penalties)==3; assert got.confidence.needs_human_review

def test_floor_is_explicit():
    d=draft(outcome=CheckerOutcome.PARTIALLY_CORRECT,reason=ResultReason.LLM_RUBRIC_EVALUATED,score=Decimal("1.00"),checker=CheckerType.LLM_RUBRIC,confidence=Decimal("0.1000"),review=True,review_reason="llm",limitations=("x",),evidence=MappingProxyType({"confidence_policy_version":"p1","confidence_reason_codes":("base",)}),rubric=(MappingProxyType({"status":"partial","evidence":()}),))
    got=prepare_result(item(),d,policy()); assert got.confidence.effective==0; assert ConfidenceReason.CONFIDENCE_FLOOR_APPLIED in got.confidence.reasons

def test_findings_derive_provenance_severity_and_remove_message():
    f=MappingProxyType({"finding_type":"typical_error","typical_error_id":E,"skill_id":S,"message":"provider secret"})
    got=prepare_result(item(),draft(findings=(f,)),policy()).findings[0]; assert got.severity=="critical" and got.snapshot_code=="E1"; assert "provider secret" not in str(got.evidence)

def test_rubric_and_general_mapping():
    fs=(MappingProxyType({"finding_type":"rubric_miss","rubric_item_id":R,"message":"x"}),MappingProxyType({"finding_type":"answer_mismatch","message":"x"}))
    got=prepare_result(item(),draft(findings=fs),policy()).findings; assert [(x.finding_type,x.severity) for x in got]==[("general","major"),("rubric","major")]

def test_duplicate_and_unknown_provenance_rejected():
    f=MappingProxyType({"finding_type":"rubric_miss","rubric_item_id":R,"message":"x"})
    with pytest.raises(InvalidCheckingResult): prepare_result(item(),draft(findings=(f,f)),policy())

def test_policy_rejects_float_and_llm_version_conflicts():
    with pytest.raises(InvalidCheckingResult): policy(missing=0.1)
    d=draft(outcome=CheckerOutcome.PARTIALLY_CORRECT,reason=ResultReason.LLM_RUBRIC_EVALUATED,score=Decimal("5.00"),checker=CheckerType.LLM_RUBRIC,confidence=Decimal(".8"),review=True,review_reason="x",evidence=MappingProxyType({"confidence_policy_version":"other","confidence_reason_codes":("x",)}))
    with pytest.raises(ConfidencePolicyConflict): prepare_result(item(),d,policy())

def test_preparation_is_stable_and_source_immutable():
    d=draft(); before=d.findings; assert prepare_result(item(),d,policy())==prepare_result(item(),d,policy()); assert d.findings is before

def test_identity_points_and_unsafe_evidence_rejected():
    with pytest.raises(InvalidCheckingResult): prepare_result(item()|{"task_version_id":I},draft(),policy())
    with pytest.raises(InvalidCheckingResult): prepare_result(item(),draft(outcome=CheckerOutcome.PARTIALLY_CORRECT,score=Decimal("5.00")).__class__(I,T,CheckerOutcome.PARTIALLY_CORRECT,CheckerType.EXACT,"v1",ResultReason.EXACT_MATCH,Decimal("5.00"),Decimal("9.00"),Decimal("1.0000"),"summary",None,None,False,None),policy())
    with pytest.raises(InvalidCheckingResult): prepare_result(item(),replace(draft(),evidence=MappingProxyType({"x":1.2})),policy())
