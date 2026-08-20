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


def llm(**kw):
    values=dict(outcome=CheckerOutcome.PARTIALLY_CORRECT,reason=ResultReason.LLM_RUBRIC_EVALUATED,score=Decimal("5.00"),checker=CheckerType.LLM_RUBRIC,confidence=Decimal("0.8000"),review=True,review_reason="llm_review",evidence=MappingProxyType({"confidence_policy_version":"p1","confidence_reason_codes":("calibrated",)}),rubric=(MappingProxyType({"status":"partial","evidence":(MappingProxyType({"source":"student_answer"}),)}),))
    values.update(kw); return draft(**values)


def test_deterministic_partial_confidence():
    d=draft(outcome=CheckerOutcome.PARTIALLY_CORRECT,reason=ResultReason.CHOICE_PARTIAL,score=Decimal("5.00"),checker=CheckerType.MULTIPLE_CHOICE)
    assert prepare_result(item(),d,policy()).confidence.effective==Decimal("1.0000")

def test_unanswered_does_not_require_review(): assert not prepare_result(item(),draft(outcome=CheckerOutcome.INCORRECT,reason=ResultReason.UNANSWERED,score=Decimal("0.00")),policy()).confidence.needs_human_review

def test_llm_always_requires_review(): assert prepare_result(item(),llm(),policy()).confidence.needs_human_review

def test_injected_llm_base_confidence(): assert prepare_result(item(),llm(confidence=Decimal("0.7123")),policy()).confidence.base==Decimal("0.7123")

def test_llm_confidence_reason_evidence_is_validated():
    with pytest.raises(InvalidCheckingResult): prepare_result(item(),llm(evidence=MappingProxyType({"confidence_policy_version":"p1","confidence_reason_codes":()})),policy())

def test_missing_evidence_penalty(): assert ConfidenceReason.MISSING_RUBRIC_EVIDENCE in prepare_result(item(),llm(rubric=(MappingProxyType({"status":"partial","evidence":()}),)),policy()).confidence.reasons

def test_missing_evidence_penalty_applies_once(): assert len([x for x in prepare_result(item(),llm(rubric=(MappingProxyType({"status":"partial","evidence":()}),MappingProxyType({"status":"not_met","evidence":()}))),policy()).confidence.penalties if x.reason is ConfidenceReason.MISSING_RUBRIC_EVIDENCE])==1

def test_model_limitations_penalty(): assert ConfidenceReason.MODEL_LIMITATIONS_PRESENT in prepare_result(item(),llm(limitations=("bounded",)),policy()).confidence.reasons

def test_model_limitations_penalty_applies_once(): assert len([x for x in prepare_result(item(),llm(limitations=("one","two"),rubric=(MappingProxyType({"status":"partial","evidence":(),"limitations":("three",)}),)),policy()).confidence.penalties if x.reason is ConfidenceReason.MODEL_LIMITATIONS_PRESENT])==1

@pytest.mark.parametrize("score",[Decimal("0.50"),Decimal("9.50")],ids=["near_zero","near_maximum"])
def test_borderline_partial_penalty(score): assert ConfidenceReason.BORDERLINE_SCORE in prepare_result(item(),llm(score=score),policy()).confidence.reasons

@pytest.mark.parametrize("outcome,score",[(CheckerOutcome.INCORRECT,Decimal("0.00")),(CheckerOutcome.CORRECT,Decimal("10.00"))],ids=["exact_zero","exact_maximum"])
def test_endpoints_are_not_borderline(outcome,score):
    d=llm(outcome=outcome,score=score); assert ConfidenceReason.BORDERLINE_SCORE not in prepare_result(item(),d,policy()).confidence.reasons

def test_floor_reason_is_persisted_in_envelope(): assert "confidence_floor_applied" in prepare_result(item(),llm(confidence=Decimal(".1"),limitations=("x",),rubric=(MappingProxyType({"status":"partial","evidence":()}),),score=Decimal(".5")),policy()).validated_result["confidence"]["reasons"]

def test_round_half_up_to_four_places():
    got=prepare_result(item(),llm(confidence=Decimal("0.8000"),score=Decimal("0.50")),policy(missing=Decimal("0.0000"),limits=Decimal("0.0000"),border=Decimal("0.0666"),fraction=Decimal("0.1")))
    assert got.confidence.effective==Decimal("0.7334")

def test_below_threshold_strengthens_review(): assert ConfidenceReason.BELOW_REVIEW_THRESHOLD in prepare_result(item(),llm(confidence=Decimal("0.4000")),policy()).confidence.reasons

@pytest.mark.parametrize("value",[Decimal("NaN"),Decimal("Infinity"),Decimal("-Infinity")],ids=["nan","positive_infinity","negative_infinity"])
def test_nonfinite_policy_decimal_rejected(value):
    with pytest.raises(InvalidCheckingResult): policy(missing=value)

def test_canonical_decimal_serialization(): assert prepare_result(item(),draft(),policy()).validated_result["confidence"]["effective"]=="1.0000"

def test_required_rubric_severity(): assert prepare_result(item(),draft(findings=(MappingProxyType({"finding_type":"rubric_miss","rubric_item_id":R,"message":"x"}),)),policy()).findings[0].severity=="major"

def test_optional_rubric_severity():
    changed=item(); changed["methodology"]["rubric"]["items"][0]["required"]=False
    assert prepare_result(changed,draft(findings=(MappingProxyType({"finding_type":"rubric_miss","rubric_item_id":R,"message":"x"}),)),policy()).findings[0].severity=="minor"

def test_typical_error_linked_skill_mismatch():
    with pytest.raises(InvalidCheckingResult): prepare_result(item(),draft(findings=(MappingProxyType({"finding_type":"typical_error","typical_error_id":E,"skill_id":R,"message":"x"}),)),policy())

@pytest.mark.parametrize("finding",[
 MappingProxyType({"finding_type":"rubric_miss","rubric_item_id":E,"message":"x"}),
 MappingProxyType({"finding_type":"typical_error","typical_error_id":R,"message":"x"}),
 MappingProxyType({"finding_type":"answer_mismatch","skill_id":R,"message":"x"})],ids=["unknown_rubric","unknown_typical_error","unknown_skill"])
def test_unknown_finding_provenance(finding):
    with pytest.raises(InvalidCheckingResult): prepare_result(item(),draft(findings=(finding,)),policy())

def test_deterministic_mismatch_has_no_invented_findings(): assert prepare_result(item(),draft(outcome=CheckerOutcome.INCORRECT,reason=ResultReason.EXACT_MISMATCH,score=Decimal("0.00")),policy()).findings==()

def test_oversized_evidence_rejected():
    with pytest.raises(InvalidCheckingResult): prepare_result(item(),replace(draft(),evidence=MappingProxyType({"x":"z"*17000})),policy())

def test_assessment_item_identity_mismatch():
    with pytest.raises(InvalidCheckingResult): prepare_result(item()|{"assessment_item_id":T},draft(),policy())

def test_task_version_identity_mismatch():
    with pytest.raises(InvalidCheckingResult): prepare_result(item()|{"task_version_id":I},draft(),policy())

def test_prepared_envelope_changes_with_policy():
    assert prepare_result(item(),draft(),policy()).validated_result!=prepare_result(item(),draft(),ConfidenceGatePolicy("p2",Decimal(".5"),Decimal(".1"),Decimal(".1"),Decimal(".1"),Decimal(".1"))).validated_result

def test_prepared_envelope_changes_with_result_reason():
    a=draft(outcome=CheckerOutcome.INCORRECT,reason=ResultReason.EXACT_MISMATCH,score=Decimal("0.00")); b=replace(a,reason_code=ResultReason.NUMERIC_MISMATCH)
    assert prepare_result(item(),a,policy()).validated_result!=prepare_result(item(),b,policy()).validated_result

def test_prepared_envelope_changes_with_finding():
    a=prepare_result(item(),draft(),policy()); b=prepare_result(item(),draft(findings=(MappingProxyType({"finding_type":"answer_mismatch","message":"secret"}),)),policy()); assert a.validated_result!=b.validated_result

def test_exception_is_bounded_and_privacy_safe():
    secret="student-secret-raw-answer"
    with pytest.raises(InvalidCheckingResult) as exc: prepare_result(item(),replace(draft(),evidence=MappingProxyType({"x":secret,"bad":1.2})),policy())
    assert secret not in str(exc.value) and len(str(exc.value))<64
