"""Pure Phase 4.9 result preparation and transport-neutral finalization boundary."""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

from app.application.checking_routing import CheckerOutcome, CheckerResultDraft, CheckerType, ResultReason

RESULT_PERSISTENCE_VERSION = "checking_result_persistence_v1"
FINDING_SCHEMA_VERSION = "checking_finding_v1"
CONFIDENCE_GATE_SCHEMA_VERSION = "checking_confidence_gate_v1"
OBSERVABILITY_SCHEMA_VERSION = "checking_observability_v1"
_Q4 = Decimal("0.0001")


class CheckingResultError(ValueError):
    def __init__(self, code: str): self.code = code; super().__init__(code)
class InvalidCheckingResult(CheckingResultError): pass
class ConfidencePolicyConflict(CheckingResultError): pass
class ResultReplayConflict(CheckingResultError): pass


class ConfidenceReason(str, Enum):
    DETERMINISTIC_PROOF="deterministic_proof"; UNANSWERED="unanswered"
    MANUAL_REQUIRED="manual_required"; INSUFFICIENT_RUBRIC="insufficient_rubric"; UNCLEAR="unclear"
    LLM_CALIBRATED_BASE="llm_calibrated_base"; MISSING_RUBRIC_EVIDENCE="missing_rubric_evidence"
    MODEL_LIMITATIONS_PRESENT="model_limitations_present"; BORDERLINE_SCORE="borderline_score"
    PROVIDER_FAILURE="provider_failure"; STRUCTURED_OUTPUT_INVALID="structured_output_invalid"
    CONFIDENCE_FLOOR_APPLIED="confidence_floor_applied"; BELOW_REVIEW_THRESHOLD="below_review_threshold"


def _decimal(value: Any, code: str, maximum: Decimal = Decimal("1")) -> None:
    if type(value) is not Decimal or not value.is_finite() or not Decimal(0) <= value <= maximum or value.as_tuple().exponent < -4:
        raise InvalidCheckingResult(code)


@dataclass(frozen=True)
class ConfidenceGatePolicy:
    semantic_version: str
    review_threshold: Decimal
    missing_evidence_penalty: Decimal
    model_limitations_penalty: Decimal
    borderline_score_fraction: Decimal
    borderline_score_penalty: Decimal
    def __post_init__(self):
        if type(self.semantic_version) is not str or not self.semantic_version.strip() or len(self.semantic_version)>64:
            raise InvalidCheckingResult("invalid_confidence_policy")
        for value in (self.review_threshold,self.missing_evidence_penalty,self.model_limitations_penalty,self.borderline_score_penalty):
            _decimal(value,"invalid_confidence_policy")
        _decimal(self.borderline_score_fraction,"invalid_confidence_policy",Decimal("0.5"))


@dataclass(frozen=True)
class ConfidencePenalty:
    reason: ConfidenceReason
    amount: Decimal


@dataclass(frozen=True)
class ConfidenceAssessment:
    schema_version: str; base: Decimal; effective: Decimal; policy_version: str
    reasons: tuple[ConfidenceReason,...]; penalties: tuple[ConfidencePenalty,...]
    total_penalty: Decimal; needs_human_review: bool; review_reason: str|None


@dataclass(frozen=True)
class FindingDraft:
    finding_type: str; severity: str; confidence: Decimal
    rubric_item_id: UUID|None=None; typical_error_id: UUID|None=None; skill_id: UUID|None=None
    snapshot_code: str|None=None; snapshot_title: str|None=None; snapshot_criterion: str|None=None
    evidence: Mapping[str,Any]=MappingProxyType({})


@dataclass(frozen=True)
class PreparedCheckingResult:
    assessment_item_id: UUID; task_version_id: UUID; checker_type: str; checker_version: str
    schema_version: str; outcome: str; reason_code: str; score_suggested: Decimal|None; max_score: Decimal
    confidence: ConfidenceAssessment; summary: str; student_feedback_draft: str|None; teacher_summary: str|None
    model_limitations: tuple[str,...]; findings: tuple[FindingDraft,...]; rubric_items: tuple[Mapping[str,Any],...]
    validated_result: Mapping[str,Any]


@dataclass(frozen=True)
class RunObservability:
    schema_version: str; run_id: UUID; run_status: str; threshold_policy_version: str
    item_count: int; result_count: int; review_required_count: int; finding_count: int
    result_counts_by_status: tuple[tuple[str,int],...]; result_counts_by_checker_type: tuple[tuple[str,int],...]
    result_counts_by_reason: tuple[tuple[str,int],...]; model_attempt_counts_by_status: tuple[tuple[str,int],...]=()
    provider_retry_count: int=0; total_measured_provider_latency: int=0; input_tokens: int=0
    output_tokens: int=0; cached_tokens: int=0; costs: tuple[tuple[str,str,str,str],...]=()


class CheckingResultPersistence(Protocol):
    async def finalize(self, run_id: UUID, expected_row_version: int, policy: ConfidenceGatePolicy,
                       drafts: tuple[CheckerResultDraft,...]) -> RunObservability: ...


def _uuid(value: Any, code="invalid_result_identity") -> UUID:
    try: parsed=UUID(value)
    except (ValueError,TypeError,AttributeError): raise InvalidCheckingResult(code) from None
    if value != str(parsed): raise InvalidCheckingResult(code)
    return parsed


def _safe(value: Any, *, limit=16000) -> Any:
    def walk(v):
        if v is None or type(v) in (str,bool,int): return v
        if type(v) is Decimal:
            if not v.is_finite(): raise InvalidCheckingResult("unsafe_evidence")
            return format(v,"f")
        if isinstance(v, Mapping):
            if any(type(k) is not str for k in v): raise InvalidCheckingResult("unsafe_evidence")
            return {k:walk(v[k]) for k in sorted(v)}
        if isinstance(v,(tuple,list)): return [walk(x) for x in v]
        raise InvalidCheckingResult("unsafe_evidence")
    out=walk(value)
    if len(json.dumps(out,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode())>limit:
        raise InvalidCheckingResult("unsafe_evidence")
    return out


def _finding(item: Mapping[str,Any], raw: Mapping[str,Any], confidence: Decimal) -> FindingDraft:
    kind=raw.get("finding_type"); mapped={"typical_error":"typical_error","rubric_miss":"rubric","answer_mismatch":"general","format_problem":"general","limitation":"general"}.get(kind)
    if mapped is None: raise InvalidCheckingResult("invalid_finding_kind")
    rid=_uuid(raw.get("rubric_item_id"),"invalid_finding_provenance") if raw.get("rubric_item_id") is not None else None
    tid=_uuid(raw.get("typical_error_id"),"invalid_finding_provenance") if raw.get("typical_error_id") is not None else None
    sid=_uuid(raw.get("skill_id"),"invalid_finding_provenance") if raw.get("skill_id") is not None else None
    method=item.get("methodology",{}); rubric=(method.get("rubric") or {}).get("items",()); errors=method.get("typical_errors",()); skills=method.get("skills",())
    rr=[x for x in rubric if x.get("id")==str(rid)]; ee=[x for x in errors if x.get("id")==str(tid)]; ss={x.get("id"):x for x in skills}
    code=title=criterion=None
    if mapped=="rubric":
        if rid is None or str(rid) not in item.get("rubric_item_ids",()) or len(rr)!=1: raise InvalidCheckingResult("invalid_finding_provenance")
        criterion=rr[0].get("criterion"); severity="major" if rr[0].get("required") else "minor"
    elif mapped=="typical_error":
        if tid is None or str(tid) not in item.get("typical_error_ids",()) or len(ee)!=1: raise InvalidCheckingResult("invalid_finding_provenance")
        linked=ee[0].get("skill_id")
        if linked not in item.get("skill_ids",()) or linked not in ss or (sid is not None and str(sid)!=linked): raise InvalidCheckingResult("invalid_finding_provenance")
        sid=UUID(linked); code=ee[0].get("code"); title=ee[0].get("title"); severity=ee[0].get("severity")
        if severity not in {"info","minor","major","critical"}: raise InvalidCheckingResult("invalid_finding_severity")
    else: severity={"answer_mismatch":"major","format_problem":"minor","limitation":"info"}[kind]
    evidence=MappingProxyType({"schema_version":FINDING_SCHEMA_VERSION,"source_finding_kind":kind,
        "rubric_item_id":str(rid) if rid else None,"typical_error_id":str(tid) if tid else None,
        "skill_id":str(sid) if sid else None,"reason_code":"validated_snapshot_provenance"})
    return FindingDraft(mapped,severity,confidence,rid,tid,sid,code,title,criterion,evidence)


def prepare_result(snapshot_item: Mapping[str,Any], draft: CheckerResultDraft, policy: ConfidenceGatePolicy) -> PreparedCheckingResult:
    aid=_uuid(draft.assessment_item_id); tid=_uuid(draft.task_version_id)
    if snapshot_item.get("assessment_item_id")!=str(aid) or snapshot_item.get("task_version_id")!=str(tid): raise InvalidCheckingResult("result_identity_mismatch")
    try: points=Decimal(snapshot_item["points"])
    except Exception: raise InvalidCheckingResult("invalid_frozen_points") from None
    if type(draft.max_score) is not Decimal or draft.max_score!=points: raise InvalidCheckingResult("points_mismatch")
    _safe(draft.evidence); _safe(draft.rubric_items)
    if policy.semantic_version != draft.evidence.get("confidence_policy_version") and draft.checker_type is CheckerType.LLM_RUBRIC and draft.reason_code is ResultReason.LLM_RUBRIC_EVALUATED:
        raise ConfidencePolicyConflict("confidence_policy_mismatch")
    reasons=[]; penalties=[]
    if draft.reason_code is ResultReason.UNANSWERED: base=Decimal("1.0000"); reasons.append(ConfidenceReason.UNANSWERED)
    elif draft.outcome is CheckerOutcome.MANUAL_REQUIRED: base=Decimal("0.0000"); reasons.append(ConfidenceReason.MANUAL_REQUIRED)
    elif draft.outcome is CheckerOutcome.INSUFFICIENT_RUBRIC: base=Decimal("0.0000"); reasons.append(ConfidenceReason.INSUFFICIENT_RUBRIC)
    elif draft.outcome is CheckerOutcome.UNCLEAR:
        base=Decimal("0.0000"); reasons.append(ConfidenceReason.UNCLEAR)
        if draft.reason_code is ResultReason.LLM_PROVIDER_FAILURE: reasons.append(ConfidenceReason.PROVIDER_FAILURE)
        if draft.reason_code is ResultReason.LLM_STRUCTURED_OUTPUT_INVALID: reasons.append(ConfidenceReason.STRUCTURED_OUTPUT_INVALID)
    elif draft.checker_type is not CheckerType.LLM_RUBRIC: base=Decimal("1.0000"); reasons.append(ConfidenceReason.DETERMINISTIC_PROOF)
    else:
        base=draft.confidence; _decimal(base,"invalid_confidence"); reasons.append(ConfidenceReason.LLM_CALIBRATED_BASE)
        missing=any(x.get("status")!="unclear" and not x.get("evidence") for x in draft.rubric_items)
        if missing: penalties.append(ConfidencePenalty(ConfidenceReason.MISSING_RUBRIC_EVIDENCE,policy.missing_evidence_penalty))
        if draft.model_limitations or any(x.get("limitations") for x in draft.rubric_items): penalties.append(ConfidencePenalty(ConfidenceReason.MODEL_LIMITATIONS_PRESENT,policy.model_limitations_penalty))
        if draft.score_suggested not in (None,Decimal(0),draft.max_score):
            ratio=draft.score_suggested/draft.max_score
            if ratio<=policy.borderline_score_fraction or ratio>=Decimal(1)-policy.borderline_score_fraction:
                penalties.append(ConfidencePenalty(ConfidenceReason.BORDERLINE_SCORE,policy.borderline_score_penalty))
    reasons.extend(x.reason for x in penalties); total=sum((x.amount for x in penalties),Decimal(0)); raw=base-total
    if raw<0: effective=Decimal("0.0000"); reasons.append(ConfidenceReason.CONFIDENCE_FLOOR_APPLIED)
    else: effective=raw.quantize(_Q4,rounding=ROUND_HALF_UP)
    review=draft.needs_human_review or draft.checker_type is CheckerType.LLM_RUBRIC or draft.outcome in {CheckerOutcome.MANUAL_REQUIRED,CheckerOutcome.INSUFFICIENT_RUBRIC,CheckerOutcome.UNCLEAR} or effective<policy.review_threshold or (draft.checker_type is not CheckerType.LLM_RUBRIC and effective!=Decimal("1.0000"))
    if effective<policy.review_threshold: reasons.append(ConfidenceReason.BELOW_REVIEW_THRESHOLD)
    review_reason=(draft.needs_human_review_reason or reasons[-1].value) if review else None
    assessment=ConfidenceAssessment(CONFIDENCE_GATE_SCHEMA_VERSION,base,effective,policy.semantic_version,tuple(reasons),tuple(penalties),total.quantize(_Q4,rounding=ROUND_HALF_UP),review,review_reason)
    findings=tuple(sorted((_finding(snapshot_item,x,effective) for x in draft.findings),key=lambda x:(x.finding_type,str(x.rubric_item_id or ""),str(x.typical_error_id or ""),str(x.skill_id or ""),x.snapshot_code or "")))
    identities=[(x.finding_type,x.rubric_item_id,x.typical_error_id,x.skill_id,x.snapshot_code) for x in findings]
    if len(identities)!=len(set(identities)): raise InvalidCheckingResult("duplicate_finding")
    confidence_json={"schema_version":assessment.schema_version,"base":format(base,".4f"),"effective":format(effective,".4f"),"policy_version":policy.semantic_version,"reasons":[x.value for x in reasons],"penalties":[{"reason":x.reason.value,"amount":format(x.amount,"f")} for x in penalties],"total_penalty":format(assessment.total_penalty,".4f"),"needs_human_review":review,"review_reason":review_reason}
    envelope=MappingProxyType({"schema_version":RESULT_PERSISTENCE_VERSION,"source_schema_version":draft.schema_version,"checker_version":draft.checker_version,"reason_code":draft.reason_code.value,"result_status":draft.outcome.value,"score_suggested":format(draft.score_suggested,"f") if draft.score_suggested is not None else None,"max_score":format(points,"f"),"confidence":confidence_json,"rubric_items":_safe(draft.rubric_items)})
    return PreparedCheckingResult(aid,tid,draft.checker_type.value,draft.checker_version,draft.schema_version,draft.outcome.value,draft.reason_code.value,draft.score_suggested,points,assessment,draft.summary,draft.student_feedback_draft,draft.teacher_summary,draft.model_limitations,findings,tuple(MappingProxyType(_safe(x)) for x in draft.rubric_items),envelope)


class CheckingResultFinalizationService:
    def __init__(self, persistence: CheckingResultPersistence): self.persistence=persistence
    async def finalize(self, run_id: UUID, expected_run_row_version: int, policy: ConfidenceGatePolicy,
                       drafts: tuple[CheckerResultDraft,...]) -> RunObservability:
        return await self.persistence.finalize(run_id,expected_run_row_version,policy,drafts)
