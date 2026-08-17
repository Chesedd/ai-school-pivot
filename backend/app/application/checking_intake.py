"""Deterministic, privacy-minimized Checking intake (no routing or checking)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from app.application.checking import CreateRunCommand
from app.application.checking_handoff import CheckingHandoff

SNAPSHOT_SCHEMA_VERSION = "checking_input_v1"
HANDOFF_VERSION = 1
ROUTING_CONTRACT_VERSION = "checking_routing_contract_v1"


class CheckingIntakeError(Exception): pass
class SubmissionNotFound(CheckingIntakeError): pass
class SubmissionNotSubmitted(CheckingIntakeError): pass
class InvalidCheckingInput(CheckingIntakeError): pass
class HistoricalMethodologyNotFound(CheckingIntakeError): pass


@dataclass(frozen=True)
class CheckingIntakeRequest:
    submission_id: UUID
    request_key: str
    routing_version: str
    checker_set_version: str
    threshold_policy_version: str
    prompt_model_policy_version: str
    supersedes_run_id: UUID | None = None


class CheckingIntakeUnitOfWork(Protocol):
    async def __aenter__(self): ...
    async def __aexit__(self, exc_type, exc, tb): ...
    async def load_locked_handoff(self, submission_id: UUID) -> CheckingHandoff: ...
    async def load_methodologies(self, version_ids: tuple[UUID, ...]) -> dict[UUID, dict[str, Any]]: ...
    async def validate_supersedes(self, run_id: UUID, submission_id: UUID) -> None: ...
    async def create_run(self, command: CreateRunCommand): ...
    async def commit(self) -> None: ...


def _decimal(value: Decimal) -> str:
    if not value.is_finite(): raise InvalidCheckingInput("non-finite decimal")
    if value == 0: return "0"
    return format(value.normalize(), "f")


def _json_value(value: Any) -> Any:
    if isinstance(value, UUID): return str(value).lower()
    if isinstance(value, Decimal): return _decimal(value)
    if isinstance(value, datetime):
        if value.tzinfo is None: raise InvalidCheckingInput("timestamp must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict): return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_json_value(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)): return value
    if isinstance(value, float): raise InvalidCheckingInput("floats are not canonical input")
    raise InvalidCheckingInput("unsupported canonical input type")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize allowlisted values; callers own semantic collection ordering."""
    try:
        return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidCheckingInput("input is not canonical JSON") from exc


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _methodology(value: dict[str, Any]) -> dict[str, Any]:
    """Copy the exact documented allowlist and impose semantic ordering."""
    options = sorted(value.get("choice_options", ()), key=lambda x: (x["order_index"], str(x["id"])))
    option_ids = {str(x["id"]): x for x in options}
    accepted = []
    for answer in sorted(value.get("accepted_answers", ()), key=lambda x: str(x["id"])):
        ids = sorted((str(x) for x in answer.get("option_ids", ())))
        if any(x not in option_ids for x in ids): raise InvalidCheckingInput("accepted choice is outside task version")
        accepted.append({k: answer.get(k) for k in ("id", "answer_value", "tolerance", "unit",
            "normalization_rule", "value_kind", "canonical_text", "canonical_decimal", "absolute_tolerance",
            "relative_tolerance", "unit_code", "normalization_policy_code", "normalization_policy_version")} | {"option_ids": ids})
    policy = value.get("choice_scoring_policy")
    if policy is not None:
        rules=[]
        for rule in policy.get("option_rules", ()):
            oid=str(rule["option_id"])
            if oid not in option_ids or option_ids[oid]["option_key"] != rule["option_key"]:
                raise InvalidCheckingInput("scoring rule is outside task version")
            rules.append({k: rule[k] for k in ("option_id", "option_key", "role", "weight")})
        policy={"mode":policy["mode"], "policy_version":policy["policy_version"],
                "option_rules":sorted(rules,key=lambda x:(str(x["option_id"]),x["option_key"]))}
    rubric=value.get("rubric")
    if rubric is not None:
        rubric={k:rubric.get(k) for k in ("id","grading_mode","max_score","notes")} | {
            "items":sorted(rubric.get("items",()),key=lambda x:(x["order_index"],str(x["id"])))}
    solution=value.get("expected_solution")
    if solution is not None: solution={k:solution.get(k) for k in ("id","solution_text","final_answer","solution_steps")}
    return {"statement":value["statement"],"task_type":value["task_type"],"answer_format":value["answer_format"],
        "skills":sorted(value.get("skills",()),key=lambda x:str(x["id"])), "expected_solution":solution,
        "accepted_answers":accepted,"choice_options":options,"choice_scoring_policy":policy,"rubric":rubric,
        "typical_errors":sorted(value.get("typical_errors",()),key=lambda x:str(x["id"]))}


def build_snapshot(handoff: CheckingHandoff, methodologies: dict[UUID, dict[str, Any]]) -> dict[str, Any]:
    seen_items:set[UUID]=set(); seen_order:set[tuple[int,UUID]]=set(); result=[]
    for item in sorted(handoff.items,key=lambda x:(x.position,x.assessment_item_id)):
        if item.assessment_item_id in seen_items or (item.position,item.assessment_item_id) in seen_order:
            raise InvalidCheckingInput("duplicate handoff item identity")
        seen_items.add(item.assessment_item_id); seen_order.add((item.position,item.assessment_item_id))
        if not item.points.is_finite() or item.points <= 0: raise InvalidCheckingInput("invalid frozen points")
        if (item.raw_answer is None) != (item.normalized_answer is None):
            raise InvalidCheckingInput("raw and normalized answer presence differs")
        source=methodologies.get(item.task_version_id)
        if source is None: raise HistoricalMethodologyNotFound("historical task version is missing")
        methodology=_methodology(source)
        if item.answer_format != methodology["answer_format"]: raise InvalidCheckingInput("answer format mismatch")
        provenance={"rubric_item_ids":[str(x["id"]) for x in (methodology["rubric"] or {}).get("items",())],
            "typical_error_ids":[str(x["id"]) for x in methodology["typical_errors"]],
            "skill_ids":[str(x["id"]) for x in methodology["skills"]]}
        for ids in provenance.values():
            if len(ids)!=len(set(ids)): raise InvalidCheckingInput("duplicate methodology provenance")
        result.append({"assessment_item_id":str(item.assessment_item_id),"task_version_id":str(item.task_version_id),
            "position":item.position,"points":format(item.points,".2f"),"answer_format":item.answer_format,
            "raw_answer":item.raw_answer,"normalized_answer":item.normalized_answer,"methodology":methodology,**provenance})
    return {"snapshot_schema_version":SNAPSHOT_SCHEMA_VERSION,"handoff_version":HANDOFF_VERSION,
        "routing_contract_version":ROUTING_CONTRACT_VERSION,
        "source_contract_versions":{"assessment_checking_handoff":"v1","content_bank_methodology":"typed_v1"},
        "submission_id":str(handoff.submission_id),"submitted_at":_json_value(handoff.submitted_at),"items":result}


def canonical_run_request(request: CheckingIntakeRequest, fingerprint: str) -> dict[str, Any]:
    return {"submission_id":str(request.submission_id),"input_fingerprint":fingerprint,
        "snapshot_schema_version":SNAPSHOT_SCHEMA_VERSION,"routing_version":request.routing_version,
        "checker_set_version":request.checker_set_version,"threshold_policy_version":request.threshold_policy_version,
        "prompt_model_policy_version":request.prompt_model_policy_version,
        "supersedes_run_id":str(request.supersedes_run_id) if request.supersedes_run_id else None}


class CheckingIntakeService:
    def __init__(self, uow_factory): self.uow_factory=uow_factory

    async def create(self, request: CheckingIntakeRequest):
        if not request.request_key or request.request_key != request.request_key.strip() or len(request.request_key)>128:
            raise InvalidCheckingInput("invalid request key")
        async with self.uow_factory() as uow:
            handoff=await uow.load_locked_handoff(request.submission_id)
            methods=await uow.load_methodologies(tuple(x.task_version_id for x in handoff.items))
            snapshot=_json_value(build_snapshot(handoff,methods)); fingerprint=sha256_hex(snapshot)
            if request.supersedes_run_id: await uow.validate_supersedes(request.supersedes_run_id,request.submission_id)
            request_hash=sha256_hex(canonical_run_request(request,fingerprint))
            run=await uow.create_run(CreateRunCommand(request.submission_id,request.request_key,request_hash,HANDOFF_VERSION,
                snapshot,fingerprint,SNAPSHOT_SCHEMA_VERSION,request.routing_version,request.checker_set_version,
                request.threshold_policy_version,request.prompt_model_policy_version,request.supersedes_run_id))
            await uow.commit(); return run
