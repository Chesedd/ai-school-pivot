"""Input-only test adapters for the Phase 4.10 production composition."""
import itertools
import json
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID, uuid5, NAMESPACE_URL

from app.application.checking_acceptance import ObservedCheckingResultV2
from app.application.checking_deterministic import execute_deterministic
from app.application.checking_llm_rubric import ConfidencePolicy, LLMRubricChecker, OUTPUT_SCHEMA_VERSION, SYSTEM_MESSAGE
from app.application.checking_provider import (AttemptDisposition, AttemptState, PromptSpec, ProviderExecutionKey,
    ProviderExecutionService, ProviderFailure, ProviderResponse, ProviderUsage)
from app.application.checking_results import ConfidenceGatePolicy, prepare_result
from app.application.checking_routing import CheckerRequest, CheckerType, route_snapshot
from app.application.student_assessments import normalize_answer


class RecordingAttemptStore:
    def __init__(self): self.records=[]; self.attempt_no=0
    async def replay_or_claim(self,key,request,prompt,maximum_attempts):
        self.attempt_no += 1
        return AttemptState(uuid5(NAMESPACE_URL,f"{key}:{self.attempt_no}"),self.attempt_no,"running",AttemptDisposition.CLAIMED,request.request_fingerprint)
    async def finalize(self,key,attempt,**values):
        self.records.append((attempt,values))
        return AttemptState(attempt.attempt_id,attempt.attempt_no,values["status"],AttemptDisposition.TERMINAL_EXISTING,
            attempt.request_fingerprint,values.get("validated_output"),values.get("error_code"))


class FixtureProvider:
    def __init__(self, specification): self.specification=specification; self.calls=0
    async def evaluate(self,request):
        self.calls += 1
        mode=self.specification.get("mode","success")
        if mode=="failure": raise ProviderFailure("authentication")
        candidate=dict(self.specification.get("candidate",{}))
        if mode=="malformed":
            candidate["rubric_items"]=[dict(candidate["rubric_items"][0],suggested_points="2.0")]
        return ProviderResponse(f"fixture-{self.calls}",json.dumps(candidate,separators=(",",":")),
            usage=ProviderUsage(17,11),latency_ms=0)


async def execute_golden_case(case_input, case_id):
    """Execute only ``input`` and ``case_id``; expectations cannot cross this API."""
    def thaw(value):
        if isinstance(value,dict) or hasattr(value,"items"): return {key:thaw(child) for key,child in value.items()}
        if isinstance(value,(list,tuple)): return [thaw(child) for child in value]
        return value
    raw=thaw(case_input); snapshot=raw["snapshot"]; item=snapshot["items"][0]
    if "normalized_answer" not in item:
        item["normalized_answer"]=None if item.get("raw_answer") is None else normalize_answer(item["answer_format"],item["raw_answer"])
    decision=route_snapshot(snapshot)[0]; checkers=None; store=None
    if decision.checker_type is CheckerType.LLM_RUBRIC and decision.execution_required:
        store=RecordingAttemptStore(); provider=FixtureProvider(raw.get("provider",{})); clock=itertools.count(100,1)
        service=ProviderExecutionService(store,provider,sleeper=lambda _: _nothing(),jitter=lambda:0,monotonic=lambda:next(clock))
        prompt=PromptSpec("checking.llm-rubric","1.0.0",SYSTEM_MESSAGE,OUTPUT_SCHEMA_VERSION)
        checker=LLMRubricChecker(service,ProviderExecutionKey(uuid5(NAMESPACE_URL,case_id),UUID(item["assessment_item_id"])),
            provider_id="acceptance-fake",model_id="acceptance-fake-v2",prompt=prompt,settings={"temperature":"0"},
            confidence_policy=ConfidencePolicy("confidence_v1",Decimal("0.7500"),("rubric_evidence",)))
        checkers={CheckerType.LLM_RUBRIC:checker}
    draft=await execute_deterministic(CheckerRequest(item,decision),checkers)
    prepared=prepare_result(item,draft,ConfidenceGatePolicy("confidence_v1",Decimal("0.5000"),*(Decimal("0.1000"),)*4))
    findings=tuple(MappingProxyType({"finding_type":x.finding_type,"rubric_item_id":str(x.rubric_item_id) if x.rubric_item_id else None,
        "typical_error_id":str(x.typical_error_id) if x.typical_error_id else None,"skill_id":str(x.skill_id) if x.skill_id else None,"code":x.snapshot_code}) for x in prepared.findings)
    terminal=store.records[-1][1] if store and store.records else None
    response=terminal.get("response") if terminal else None
    return ObservedCheckingResultV2(case_id,prepared.checker_type,prepared.outcome,prepared.reason_code,prepared.score_suggested,
        prepared.max_score,prepared.confidence.needs_human_review,findings,False,
        prepared.reason_code != "llm_structured_output_invalid",prepared.reason_code == "llm_provider_failure",
        terminal.get("measured_latency_ms",0) if terminal else 0,
        response.usage.input_tokens if response and response.usage else 0,response.usage.output_tokens if response and response.usage else 0,
        Decimal("0"),prepared.confidence.review_reason,prepared.confidence.policy_version,prepared.confidence.effective,
        tuple(x.value for x in prepared.confidence.reasons))


async def _nothing(): pass
