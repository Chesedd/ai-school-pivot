"""Anonymous, deterministic paper-checking intake (no provider execution)."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.application.checking import CreatePaperRunCommand
from app.application.checking_intake import (_json_value, _methodology, sha256_hex,
    InvalidCheckingInput, HistoricalMethodologyNotFound)
from app.application.checking_routing import ROUTING_CONTRACT_VERSION

PAPER_SNAPSHOT_SCHEMA_VERSION = "checking_input_paper_v1"
PAPER_HANDOFF_VERSION = 1
PAPER_POLICY_COMPILER_VERSION = "1.0.0"
PAPER_PROMPT_POLICY_VERSION = "1.0.0"

class PaperCheckingIntakeError(Exception): pass
class PaperGradingPolicyIncomplete(PaperCheckingIntakeError): pass
class PaperGradingPolicyLocked(PaperCheckingIntakeError): pass

@dataclass(frozen=True)
class PaperCheckingPage:
    scan_page_id: UUID; page_order: int; width_px: int; height_px: int
    coordinate_space_version: str; content_fingerprint: str; derived_render_artifact_id: UUID

@dataclass(frozen=True)
class PaperCheckingItem:
    assessment_item_id: UUID; task_version_id: UUID; position: int; points: Decimal; answer_format: str

@dataclass(frozen=True)
class FrozenPaperPolicy:
    revision_id: UUID; revision: int; schema_version: str; fingerprint: str; policy: dict[str, Any]

@dataclass(frozen=True)
class PaperCheckingHandoff:
    paper_submission_id: UUID; batch_id: UUID; assignment_id: UUID
    assigned_variant_id: UUID; grouping_revision_id: UUID
    pages: tuple[PaperCheckingPage, ...]; items: tuple[PaperCheckingItem, ...]
    grading_policy: FrozenPaperPolicy

@dataclass(frozen=True)
class PaperCheckingIntakeRequest:
    paper_submission_id: UUID; request_key: str; routing_version: str
    checker_set_version: str; threshold_policy_version: str; prompt_model_policy_version: str
    supersedes_run_id: UUID | None = None


def build_paper_snapshot(handoff: PaperCheckingHandoff,
                         methodologies: dict[UUID, dict[str, Any]]) -> dict[str, Any]:
    pages = sorted(handoff.pages, key=lambda x: x.page_order)
    if not pages or [x.page_order for x in pages] != list(range(len(pages))):
        raise InvalidCheckingInput("paper page order must be contiguous")
    page_values=[]
    for page in pages:
        if (page.width_px <= 0 or page.height_px <= 0 or
            page.coordinate_space_version != "normalized_upright_v1" or
            len(page.content_fingerprint) != 64):
            raise InvalidCheckingInput("invalid immutable paper page")
        page_values.append({"scan_page_id":str(page.scan_page_id),"page_order":page.page_order,
            "width":page.width_px,"height":page.height_px,"coordinate_space":page.coordinate_space_version,
            "content_fingerprint":page.content_fingerprint,
            "derived_render_artifact_id":str(page.derived_render_artifact_id)})
    items=[]
    seen=set()
    for item in sorted(handoff.items,key=lambda x:(x.position,x.assessment_item_id)):
        if item.assessment_item_id in seen or not item.points.is_finite() or item.points <= 0:
            raise InvalidCheckingInput("invalid paper item")
        seen.add(item.assessment_item_id)
        source=methodologies.get(item.task_version_id)
        if source is None: raise HistoricalMethodologyNotFound("historical task version is missing")
        method=_methodology(source)
        if method["answer_format"] != item.answer_format: raise InvalidCheckingInput("answer format mismatch")
        items.append({"assessment_item_id":str(item.assessment_item_id),"task_version_id":str(item.task_version_id),
            "position":item.position,"points":format(item.points,".2f"),"answer_format":item.answer_format,
            "methodology":method,"rubric_item_ids":[str(x["id"]) for x in (method["rubric"] or {}).get("items",())],
            "typical_error_ids":[str(x["id"]) for x in method["typical_errors"]],
            "skill_ids":[str(x["id"]) for x in method["skills"]]})
    policy=handoff.grading_policy
    return _json_value({"snapshot_schema_version":PAPER_SNAPSHOT_SCHEMA_VERSION,
        "handoff_version":PAPER_HANDOFF_VERSION,"routing_contract_version":ROUTING_CONTRACT_VERSION,
        "source_contract_versions":{"scan_page":"normalized_upright_v1","content_bank_methodology":"typed_v1","grading_policy":"paper_grading_policy.v1"},
        "paper_submission_id":str(handoff.paper_submission_id),"batch_id":str(handoff.batch_id),
        "assignment_id":str(handoff.assignment_id),"assigned_variant_id":str(handoff.assigned_variant_id),
        "grouping_revision_id":str(handoff.grouping_revision_id),
        "grading_policy":{"revision_id":str(policy.revision_id),"revision":policy.revision,
            "schema_version":policy.schema_version,"fingerprint":policy.fingerprint,"policy":policy.policy},
        "pages":page_values,"items":items})


def canonical_paper_run_request(request: PaperCheckingIntakeRequest, fingerprint: str) -> dict[str, Any]:
    return {"execution_target_kind":"paper","paper_submission_id":str(request.paper_submission_id),
        "input_fingerprint":fingerprint,"snapshot_schema_version":PAPER_SNAPSHOT_SCHEMA_VERSION,
        "routing_version":request.routing_version,"checker_set_version":request.checker_set_version,
        "threshold_policy_version":request.threshold_policy_version,
        "prompt_model_policy_version":request.prompt_model_policy_version,
        "supersedes_run_id":str(request.supersedes_run_id) if request.supersedes_run_id else None}

class PaperCheckingIntakeService:
    def __init__(self,uow_factory): self.uow_factory=uow_factory
    async def create(self,request: PaperCheckingIntakeRequest):
        if not request.request_key or request.request_key != request.request_key.strip() or len(request.request_key)>128:
            raise InvalidCheckingInput("invalid request key")
        async with self.uow_factory() as uow:
            handoff=await uow.load_locked_handoff(request.paper_submission_id)
            methods=await uow.load_methodologies(tuple(x.task_version_id for x in handoff.items))
            snapshot=build_paper_snapshot(handoff,methods); fingerprint=sha256_hex(snapshot)
            request_hash=sha256_hex(canonical_paper_run_request(request,fingerprint))
            run=await uow.create_paper_run(CreatePaperRunCommand(request.paper_submission_id,
                request.request_key,request_hash,PAPER_HANDOFF_VERSION,snapshot,fingerprint,
                PAPER_SNAPSHOT_SCHEMA_VERSION,request.routing_version,request.checker_set_version,
                request.threshold_policy_version,request.prompt_model_policy_version,request.supersedes_run_id))
            await uow.mark_checking_started(handoff.batch_id)
            await uow.commit(); return run
