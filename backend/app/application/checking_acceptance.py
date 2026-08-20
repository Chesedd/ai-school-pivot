"""Versioned, provider-neutral Checking acceptance contract.

The boundary deliberately accepts already validated technical observations.  It
does not execute (or select) an LLM provider and never carries answer content.
"""
from __future__ import annotations

import hashlib
import json
import re
import itertools
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any
from uuid import UUID

from app.application.checking_deterministic import execute_deterministic
from app.application.checking_llm_rubric import ConfidencePolicy, LLMRubricChecker, OUTPUT_SCHEMA_VERSION, SYSTEM_MESSAGE
from app.application.checking_provider import (AttemptDisposition, AttemptState, PromptSpec, ProviderExecutionKey,
    ProviderExecutionService, ProviderFailure, ProviderResponse)
from app.application.checking_results import ConfidenceGatePolicy, prepare_result
from app.application.checking_routing import CheckerRequest, CheckerType, route_snapshot
from app.application.student_assessments import normalize_answer

GOLDEN_DATASET_VERSION = "checking_golden_dataset_v1"
ACCEPTANCE_REPORT_VERSION = "checking_acceptance_report_v1"
ACCEPTANCE_THRESHOLDS_VERSION = "checking_acceptance_thresholds_v1"

_CHECKERS = {"exact", "multiple_choice", "numeric", "structured_expression", "llm_rubric", "manual_required"}
_OUTCOMES = {"correct", "incorrect", "partially_correct", "unclear", "insufficient_rubric", "manual_required"}
_REASONS = {
    "unanswered", "exact_match", "exact_mismatch", "choice_match", "choice_mismatch", "choice_partial",
    "unknown_choice_option", "malformed_normalized_answer", "invalid_exact_methodology",
    "invalid_choice_methodology", "numeric_match", "numeric_mismatch", "invalid_numeric_methodology",
    "expression_identity_match", "expression_equivalence_unproven", "invalid_expression_methodology",
    "routing_insufficient_rubric", "routing_manual_required", "llm_rubric_evaluated",
    "llm_provider_failure", "llm_structured_output_invalid", "llm_invalid_methodology",
}
_ID = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?\Z")
_FINDING_KEYS = {"finding_type", "rubric_item_id", "typical_error_id", "skill_id", "code"}
_FORBIDDEN = {"answer", "solution", "rubric_prose", "provider_output", "raw_output", "student_id", "person_id", "assignment_id", "email", "name"}


class AcceptanceContractError(ValueError):
    """Bounded error containing a technical code only."""
    def __init__(self, code: str = "invalid_acceptance_contract"):
        self.code = code if re.fullmatch(r"[a-z0-9_]{1,64}", code) else "invalid_acceptance_contract"
        super().__init__(self.code)


def _identity(value: Any) -> str:
    if type(value) is not str or len(value) > 64:
        raise AcceptanceContractError("invalid_case_id")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        if not _ID.fullmatch(value):
            raise AcceptanceContractError("invalid_case_id")
    else:
        if value != str(parsed):
            raise AcceptanceContractError("invalid_case_id")
    return value


def _decimal(value: Any, code: str = "invalid_decimal") -> Decimal:
    if type(value) is not str:
        raise AcceptanceContractError(code)
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise AcceptanceContractError(code) from None
    plain = format(result, "f")
    if "." in plain:
        plain = plain.rstrip("0").rstrip(".")
    if result.is_zero():
        plain = "0"
    if not result.is_finite() or "e" in value.lower() or value.startswith("+"):
        raise AcceptanceContractError(code)
    return result


def _freeze(value: Any, *, depth: int = 0) -> Any:
    if depth > 8: raise AcceptanceContractError("unsafe_data")
    if value is None or type(value) in (str, bool, int): return value
    if type(value) is float or type(value) is Decimal: raise AcceptanceContractError("unsafe_data")
    if isinstance(value, Mapping):
        if any(type(k) is not str or len(k) > 64 or k.lower() in _FORBIDDEN for k in value):
            raise AcceptanceContractError("unsafe_data")
        return MappingProxyType({k: _freeze(value[k], depth=depth + 1) for k in sorted(value)})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze(v, depth=depth + 1) for v in value)
    raise AcceptanceContractError("unsafe_data")

def _freeze_input(value: Any, *, depth: int = 0) -> Any:
    """Detach executable synthetic input; it is never emitted in a report."""
    if depth > 12: raise AcceptanceContractError("unsafe_input")
    if value is None or type(value) in (str,bool,int): return value
    if isinstance(value,Mapping):
        if any(type(k) is not str or len(k)>64 for k in value): raise AcceptanceContractError("unsafe_input")
        return MappingProxyType({k:_freeze_input(value[k],depth=depth+1) for k in sorted(value)})
    if isinstance(value,Sequence) and not isinstance(value,(str,bytes,bytearray)):
        return tuple(_freeze_input(x,depth=depth+1) for x in value)
    raise AcceptanceContractError("unsafe_input")


def _finding(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not set(value) <= _FINDING_KEYS or "finding_type" not in value:
        raise AcceptanceContractError("invalid_finding")
    if type(value["finding_type"]) is not str or not _ID.fullmatch(value["finding_type"]):
        raise AcceptanceContractError("invalid_finding")
    for key in ("rubric_item_id", "typical_error_id", "skill_id"):
        if value.get(key) is not None: _identity(value[key])
    if value.get("code") is not None and (type(value["code"]) is not str or not _ID.fullmatch(value["code"])):
        raise AcceptanceContractError("invalid_finding")
    return _freeze(value)


def _finding_key(finding: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(finding.get(k) or "") for k in sorted(_FINDING_KEYS))


def _validate_result(checker: str, outcome: str, reason: str, score: Decimal | None,
                     max_score: Decimal, review: bool, findings: tuple[Mapping[str, Any], ...]) -> None:
    if checker not in _CHECKERS: raise AcceptanceContractError("unsupported_checker")
    if outcome not in _OUTCOMES: raise AcceptanceContractError("unsupported_outcome")
    if reason not in _REASONS: raise AcceptanceContractError("unsupported_reason")
    if type(review) is not bool: raise AcceptanceContractError("invalid_review")
    if max_score <= 0: raise AcceptanceContractError("invalid_max_score")
    valid = ((outcome == "correct" and score == max_score) or (outcome == "incorrect" and score == 0)
             or (outcome == "partially_correct" and score is not None and 0 < score < max_score)
             or (outcome in {"unclear", "insufficient_rubric", "manual_required"} and score is None))
    if not valid: raise AcceptanceContractError("invalid_outcome_score")
    if outcome in {"unclear", "insufficient_rubric", "manual_required"} and not review:
        raise AcceptanceContractError("invalid_review")
    keys = [_finding_key(x) for x in findings]
    if keys != sorted(keys) or len(keys) != len(set(keys)): raise AcceptanceContractError("invalid_findings")


@dataclass(frozen=True)
class GoldenCaseV1:
    case_id: str; category: str; expected_checker: str; expected_outcome: str; expected_reason: str
    expected_score: Decimal | None; max_score: Decimal; expected_review: bool
    expected_findings: tuple[Mapping[str, Any], ...] = (); input: Mapping[str, Any] = MappingProxyType({})
    expected_review_reason: str|None=None; expected_confidence_policy: str="confidence_v1"
    expected_confidence: Decimal=Decimal("1"); expected_confidence_reasons: tuple[str,...]=()
    expected_structured_output_valid: bool=True; expected_provider_failed: bool=False
    metadata: Mapping[str, Any] = MappingProxyType({})
    def __post_init__(self):
        _identity(self.case_id)
        if type(self.category) is not str or not _ID.fullmatch(self.category): raise AcceptanceContractError("invalid_category")
        findings = tuple(_finding(x) for x in self.expected_findings)
        metadata = _freeze(self.metadata); input_value=_freeze_input(self.input)
        object.__setattr__(self, "expected_findings", findings); object.__setattr__(self, "metadata", metadata); object.__setattr__(self,"input",input_value)
        _validate_result(self.expected_checker, self.expected_outcome, self.expected_reason, self.expected_score,
                         self.max_score, self.expected_review, findings)
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GoldenCaseV1":
        required={"case_id","category","input","expected_checker","expected_outcome","expected_reason","expected_score","max_score","expected_review","expected_findings","expected_review_reason","expected_confidence_policy","expected_confidence","expected_confidence_reasons","expected_structured_output_valid","expected_provider_failed"}
        if not isinstance(value, Mapping) or set(value) - required - {"metadata"} or not required <= set(value): raise AcceptanceContractError("invalid_case_schema")
        return cls(value["case_id"],value["category"],value["expected_checker"],value["expected_outcome"],value["expected_reason"],
                   None if value["expected_score"] is None else _decimal(value["expected_score"]),_decimal(value["max_score"],"invalid_max_score"),
                   value["expected_review"],tuple(value["expected_findings"]),value["input"],value["expected_review_reason"],value["expected_confidence_policy"],
                   _decimal(value["expected_confidence"]),tuple(value["expected_confidence_reasons"]),value["expected_structured_output_valid"],value["expected_provider_failed"],value.get("metadata",{}))


@dataclass(frozen=True)
class GoldenDatasetV1:
    cases: tuple[GoldenCaseV1, ...]; version: str = GOLDEN_DATASET_VERSION; description: str = "synthetic technical acceptance corpus"
    def __post_init__(self):
        if self.version != GOLDEN_DATASET_VERSION: raise AcceptanceContractError("unsupported_dataset_version")
        if type(self.description) is not str or len(self.description)>200: raise AcceptanceContractError("invalid_dataset_schema")
        cases=tuple(self.cases); ids=[x.case_id for x in cases]
        if ids != sorted(ids) or len(ids)!=len(set(ids)): raise AcceptanceContractError("invalid_case_order")
        object.__setattr__(self,"cases",cases)
    @classmethod
    def from_dict(cls,value:Mapping[str,Any])->"GoldenDatasetV1":
        if not isinstance(value,Mapping) or set(value)!={"version","description","cases"} or not isinstance(value["cases"],list): raise AcceptanceContractError("invalid_dataset_schema")
        return cls(tuple(GoldenCaseV1.from_dict(x) for x in value["cases"]),value["version"],value["description"])


@dataclass(frozen=True)
class ObservedCheckingResultV1:
    case_id:str; checker:str; outcome:str; reason:str; score:Decimal|None; max_score:Decimal; review_required:bool
    findings:tuple[Mapping[str,Any],...]=(); privacy_violation:bool=False; structured_output_valid:bool=True
    provider_failed:bool=False; latency_ms:int=0; input_tokens:int=0; output_tokens:int=0; cost:Decimal=Decimal(0)
    review_reason:str|None=None; confidence_policy:str="confidence_v1"; confidence:Decimal=Decimal("1")
    confidence_reasons:tuple[str,...]=()
    def __post_init__(self):
        _identity(self.case_id); findings=tuple(_finding(x) for x in self.findings); object.__setattr__(self,"findings",findings)
        _validate_result(self.checker,self.outcome,self.reason,self.score,self.max_score,self.review_required,findings)
        if any(type(x) is not bool for x in (self.privacy_violation,self.structured_output_valid,self.provider_failed)): raise AcceptanceContractError("invalid_observation")
        if any(type(x) is not int or x<0 for x in (self.latency_ms,self.input_tokens,self.output_tokens)): raise AcceptanceContractError("invalid_observation")
        if type(self.cost) is not Decimal or not self.cost.is_finite() or self.cost<0: raise AcceptanceContractError("invalid_observation")
        if type(self.confidence) is not Decimal or not self.confidence.is_finite() or not 0<=self.confidence<=1: raise AcceptanceContractError("invalid_observation")
    @classmethod
    def from_dict(cls,v:Mapping[str,Any])->"ObservedCheckingResultV1":
        required={"case_id","checker","outcome","reason","score","max_score","review_required","findings"}; optional={"privacy_violation","structured_output_valid","provider_failed","latency_ms","input_tokens","output_tokens","cost","review_reason","confidence_policy","confidence","confidence_reasons"}
        if not isinstance(v,Mapping) or set(v)-required-optional or not required<=set(v): raise AcceptanceContractError("invalid_observation_schema")
        return cls(v["case_id"],v["checker"],v["outcome"],v["reason"],None if v["score"] is None else _decimal(v["score"]),_decimal(v["max_score"]),v["review_required"],tuple(v["findings"]),v.get("privacy_violation",False),v.get("structured_output_valid",True),v.get("provider_failed",False),v.get("latency_ms",0),v.get("input_tokens",0),v.get("output_tokens",0),_decimal(v.get("cost","0")),v.get("review_reason"),v.get("confidence_policy","confidence_v1"),_decimal(v.get("confidence","1")),tuple(v.get("confidence_reasons",())))


@dataclass(frozen=True)
class AcceptanceThresholdPolicy:
    checker_agreement:Decimal=Decimal("1"); outcome_agreement:Decimal=Decimal("1"); reason_agreement:Decimal=Decimal("1")
    exact_score_agreement:Decimal=Decimal("1"); unsafe_auto_score_rate:Decimal=Decimal("0"); required_review_recall:Decimal=Decimal("1")
    privacy_violations:int=0; missing_results:int=0; unexpected_results:int=0; version:str=ACCEPTANCE_THRESHOLDS_VERSION
    def __post_init__(self):
        if self.version!=ACCEPTANCE_THRESHOLDS_VERSION: raise AcceptanceContractError("unsupported_threshold_version")
        for x in (self.checker_agreement,self.outcome_agreement,self.reason_agreement,self.exact_score_agreement,self.unsafe_auto_score_rate,self.required_review_recall):
            if type(x) is not Decimal or not x.is_finite() or not 0<=x<=1: raise AcceptanceContractError("invalid_threshold")
        if any(type(x) is not int or x<0 for x in (self.privacy_violations,self.missing_results,self.unexpected_results)): raise AcceptanceContractError("invalid_threshold")


@dataclass(frozen=True)
class CaseEvaluation:
    case_id:str; checker_agrees:bool; outcome_agrees:bool; reason_agrees:bool; score_agrees:bool
    review_recalled:bool; unsafe_auto_score:bool; finding_identity_agrees:bool; privacy_violation:bool
    maximum_score_agrees:bool; review_state_agrees:bool; review_reason_agrees:bool; confidence_agrees:bool
    structured_output_agrees:bool; provider_failure_agrees:bool


@dataclass(frozen=True)
class AcceptanceMetrics:
    total_cases:int; evaluated_cases:int; missing_results:int; unexpected_results:int
    checker_agreement:Decimal; outcome_agreement:Decimal; reason_agreement:Decimal; exact_score_agreement:Decimal
    score_mae:Decimal; required_review_recall:Decimal; unsafe_auto_score_count:int; unsafe_auto_score_rate:Decimal
    finding_identity_agreement:Decimal; privacy_violation_count:int
    maximum_score_agreement:Decimal; confidence_agreement:Decimal
    counts_by_checker:tuple[tuple[str,int],...]; counts_by_outcome:tuple[tuple[str,int],...]; counts_by_reason:tuple[tuple[str,int],...]
    structured_output_validity:Decimal; provider_failure_rate:Decimal; total_latency_ms:int; input_tokens:int; output_tokens:int; total_cost:Decimal


@dataclass(frozen=True)
class AcceptanceReport:
    metrics:AcceptanceMetrics; cases:tuple[CaseEvaluation,...]; corpus_fingerprint:str; observed_fingerprint:str; report_fingerprint:str
    accepted:bool; version:str=ACCEPTANCE_REPORT_VERSION
    def to_dict(self)->dict[str,Any]:
        def cv(v):
            if isinstance(v,Decimal): return format(v,".4f")
            if hasattr(v,"__dataclass_fields__"): return {k:cv(getattr(v,k)) for k in v.__dataclass_fields__}
            if isinstance(v,tuple): return [cv(x) for x in v]
            return v
        return cv(self)
    def to_json(self)->str: return json.dumps(self.to_dict(),sort_keys=True,separators=(",",":"),ensure_ascii=False)


def _jsonable(value:Any)->Any:
    if isinstance(value,Decimal): return format(value,"f")
    if isinstance(value,Mapping): return {k:_jsonable(value[k]) for k in sorted(value)}
    if hasattr(value,"__dataclass_fields__"): return {k:_jsonable(getattr(value,k)) for k in value.__dataclass_fields__}
    if isinstance(value,(tuple,list)): return [_jsonable(x) for x in value]
    return value
def _fingerprint(value:Any)->str: return hashlib.sha256(json.dumps(_jsonable(value),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
def _rate(n:int,d:int)->Decimal: return (Decimal(n)/Decimal(d)).quantize(Decimal(".0001")) if d else Decimal(0)

class _AcceptanceAttemptStore:
    async def replay_or_claim(self,key,request,prompt,maximum_attempts):
        return AttemptState(UUID("00000000-0000-0000-0000-000000000099"),1,"running",AttemptDisposition.CLAIMED,request.request_fingerprint)
    async def finalize(self,key,attempt,**values):
        return AttemptState(attempt.attempt_id,1,values["status"],AttemptDisposition.TERMINAL_EXISTING,
            attempt.request_fingerprint,values.get("validated_output"),values.get("error_code"))

async def execute_golden_case(case_input: Mapping[str,Any], case_id: str) -> ObservedCheckingResultV1:
    """Execute input-only production composition; expected fields are structurally inaccessible."""
    raw=json.loads(json.dumps(_jsonable(case_input))); snapshot=raw["snapshot"]; item=snapshot["items"][0]
    if "normalized_answer" not in item:
        item["normalized_answer"]=None if item.get("raw_answer") is None else normalize_answer(item["answer_format"],item["raw_answer"])
    decision=route_snapshot(snapshot)[0]; checkers=None; provider_failed=False; structured=True
    if decision.checker_type is CheckerType.LLM_RUBRIC and decision.execution_required:
        provider_spec=raw.get("provider",{}); mode=provider_spec.get("mode","success")
        class Provider:
            async def evaluate(self,request):
                if mode=="failure": raise ProviderFailure("authentication")
                candidate=provider_spec.get("candidate",{})
                if mode=="malformed":
                    candidate=dict(candidate); candidate["rubric_items"]=[dict(candidate["rubric_items"][0],suggested_points="2.0")]
                output=json.dumps(candidate,separators=(",",":"))
                return ProviderResponse("synthetic",output)
        clock=itertools.count(100,1)
        service=ProviderExecutionService(_AcceptanceAttemptStore(),Provider(),monotonic=lambda:next(clock))
        prompt=PromptSpec("checking.llm-rubric","1.0.0",SYSTEM_MESSAGE,OUTPUT_SCHEMA_VERSION)
        checker=LLMRubricChecker(service,ProviderExecutionKey(UUID("00000000-0000-0000-0000-000000000098"),UUID(item["assessment_item_id"])),
            provider_id="fake",model_id="fake-v1",prompt=prompt,settings={"temperature":"0"},
            confidence_policy=ConfidencePolicy("confidence_v1",Decimal("0.7500"),("rubric_evidence",)))
        checkers={CheckerType.LLM_RUBRIC:checker}; provider_failed=mode=="failure"; structured=mode!="malformed"
    draft=await execute_deterministic(CheckerRequest(item,decision),checkers)
    policy=ConfidenceGatePolicy("confidence_v1",Decimal("0.5000"),Decimal("0.1000"),Decimal("0.1000"),Decimal("0.1000"),Decimal("0.1000"))
    prepared=prepare_result(item,draft,policy)
    findings=tuple(MappingProxyType({"finding_type":x.finding_type,"rubric_item_id":str(x.rubric_item_id) if x.rubric_item_id else None,
        "typical_error_id":str(x.typical_error_id) if x.typical_error_id else None,"skill_id":str(x.skill_id) if x.skill_id else None,"code":x.snapshot_code}) for x in prepared.findings)
    return ObservedCheckingResultV1(case_id,prepared.checker_type,prepared.outcome,prepared.reason_code,prepared.score_suggested,
        prepared.max_score,prepared.confidence.needs_human_review,findings,False,structured,provider_failed,
        review_reason=prepared.confidence.review_reason,confidence_policy=prepared.confidence.policy_version,
        confidence=prepared.confidence.effective,confidence_reasons=tuple(x.value for x in prepared.confidence.reasons))


def evaluate_golden_dataset(dataset:GoldenDatasetV1, observed_results:Sequence[ObservedCheckingResultV1], thresholds:AcceptanceThresholdPolicy)->AcceptanceReport:
    if type(dataset) is not GoldenDatasetV1 or type(thresholds) is not AcceptanceThresholdPolicy: raise AcceptanceContractError("invalid_evaluation_input")
    observed=tuple(observed_results)
    if any(type(x) is not ObservedCheckingResultV1 for x in observed): raise AcceptanceContractError("invalid_evaluation_input")
    ids=[x.case_id for x in observed]
    if len(ids)!=len(set(ids)): raise AcceptanceContractError("duplicate_observed_result")
    observed=tuple(sorted(observed,key=lambda x:x.case_id)); om={x.case_id:x for x in observed}; expected={x.case_id:x for x in dataset.cases}
    common=sorted(expected.keys() & om.keys()); evaluations=[]; score_errors=[]
    for cid in common:
        e,o=expected[cid],om[cid]; score_ok=e.expected_score==o.score; required=e.expected_review
        unsafe=(not required and o.review_required is False and o.score is not None and (e.expected_outcome!=o.outcome or e.expected_score!=o.score)) or (required and not o.review_required and o.score is not None)
        confidence_ok=(e.expected_confidence_policy==o.confidence_policy and e.expected_confidence==o.confidence and e.expected_confidence_reasons==o.confidence_reasons)
        evaluations.append(CaseEvaluation(cid,e.expected_checker==o.checker,e.expected_outcome==o.outcome,e.expected_reason==o.reason,score_ok,not required or o.review_required,unsafe,tuple(map(_finding_key,e.expected_findings))==tuple(map(_finding_key,o.findings)),o.privacy_violation,e.max_score==o.max_score,e.expected_review==o.review_required,e.expected_review_reason==o.review_reason,confidence_ok,e.expected_structured_output_valid==o.structured_output_valid,e.expected_provider_failed==o.provider_failed))
        if e.expected_score is not None and o.score is not None: score_errors.append(abs(e.expected_score-o.score))
    n=len(common); required=sum(expected[x].expected_review for x in common); ev=tuple(evaluations)
    metrics=AcceptanceMetrics(len(dataset.cases),n,len(expected.keys()-om.keys()),len(om.keys()-expected.keys()),_rate(sum(x.checker_agrees for x in ev),n),_rate(sum(x.outcome_agrees for x in ev),n),_rate(sum(x.reason_agrees for x in ev),n),_rate(sum(x.score_agrees for x in ev),n),(sum(score_errors,Decimal(0))/len(score_errors)).quantize(Decimal(".0001")) if score_errors else Decimal(0),_rate(sum(x.review_recalled for x in ev if expected[x.case_id].expected_review),required),sum(x.unsafe_auto_score for x in ev),_rate(sum(x.unsafe_auto_score for x in ev),n),_rate(sum(x.finding_identity_agrees for x in ev),n),sum(x.privacy_violation for x in ev),_rate(sum(x.maximum_score_agrees for x in ev),n),_rate(sum(x.confidence_agrees for x in ev),n),tuple(sorted(Counter(x.checker for x in observed).items())),tuple(sorted(Counter(x.outcome for x in observed).items())),tuple(sorted(Counter(x.reason for x in observed).items())),_rate(sum(x.structured_output_valid for x in observed),len(observed)),_rate(sum(x.provider_failed for x in observed),len(observed)),sum(x.latency_ms for x in observed),sum(x.input_tokens for x in observed),sum(x.output_tokens for x in observed),sum((x.cost for x in observed),Decimal(0)))
    accepted=(metrics.checker_agreement>=thresholds.checker_agreement and metrics.outcome_agreement>=thresholds.outcome_agreement and metrics.reason_agreement>=thresholds.reason_agreement and metrics.exact_score_agreement>=thresholds.exact_score_agreement and metrics.finding_identity_agreement==1 and metrics.maximum_score_agreement==1 and metrics.confidence_agreement==1 and all(x.review_state_agrees and x.review_reason_agrees and x.structured_output_agrees and x.provider_failure_agrees for x in ev) and metrics.unsafe_auto_score_rate<=thresholds.unsafe_auto_score_rate and metrics.required_review_recall>=thresholds.required_review_recall and metrics.privacy_violation_count<=thresholds.privacy_violations and metrics.missing_results<=thresholds.missing_results and metrics.unexpected_results<=thresholds.unexpected_results)
    corpus=_fingerprint(dataset); observed_fp=_fingerprint(observed); body={"version":ACCEPTANCE_REPORT_VERSION,"metrics":metrics,"cases":ev,"corpus_fingerprint":corpus,"observed_fingerprint":observed_fp,"accepted":accepted}
    return AcceptanceReport(metrics,ev,corpus,observed_fp,_fingerprint(body),accepted)
