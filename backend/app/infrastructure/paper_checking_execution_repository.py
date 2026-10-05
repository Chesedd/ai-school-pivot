"""Atomic PostgreSQL persistence for paper-level checking execution."""
# ruff: noqa: E501, E701, E702
from __future__ import annotations
from dataclasses import dataclass
import json
from uuid import UUID

from sqlalchemy import func, select

from app.application.checking_provider import PromptSpec
from app.application.paper_checking_execution import (
    PAPER_CHECK_PROMPT_HASH, PAPER_CHECK_PROMPT_NAME, PAPER_CHECK_PROMPT_VERSION,
    PAPER_CHECK_SYSTEM_PROMPT, CompiledPaperRequest, PaperCheckingExecutionError,
    compile_paper_request,
)
from app.application.scan_checking_contracts import PAPER_CHECK_OUTPUT_VERSION
from app.infrastructure.authoring_models import InputArtifact
from app.infrastructure.checking_models import (CheckFinding, CheckResult, CheckRun,
    CheckerEvent, ModelRun, PaperAIRevision, PaperCheckFindingRegion)
from app.infrastructure.checking_repository import CheckingRepository


@dataclass
class PreparedExecution:
    compiled: CompiledPaperRequest
    model_run_id: UUID | None
    paper_submission_id: UUID
    artifacts: dict[str, InputArtifact]
    replay: object | None = None


class SQLAlchemyPaperCheckingExecutionRepository:
    def __init__(self, session_factory): self.session_factory=session_factory

    async def prepare(self,paper_id,run_id,provider_id,model_id):
        async with self.session_factory() as session:
          async with session.begin():
            run=await session.scalar(select(CheckRun).where(CheckRun.id==run_id).with_for_update())
            if run is None or run.paper_submission_id!=paper_id or run.submission_id is not None:
                raise PaperCheckingExecutionError("paper_check_run_not_found")
            compiled=compile_paper_request(run.input_snapshot,provider_id=provider_id,model_id=model_id,
                                           settings={"temperature":"0","max_output_tokens":8192})
            existing=await session.scalar(select(PaperAIRevision).where(PaperAIRevision.check_run_id==run.id))
            if existing is not None: return PreparedExecution(compiled,None,paper_id,{},await self._result(session,run.id))
            if run.status=="pending":
                await CheckingRepository(session).transition_run(run.id,run.row_version,"running")
            elif run.status!="running": raise PaperCheckingExecutionError("paper_check_run_not_executable")
            prompt=await CheckingRepository(session).register_prompt(PromptSpec(PAPER_CHECK_PROMPT_NAME,
                PAPER_CHECK_PROMPT_VERSION,PAPER_CHECK_SYSTEM_PROMPT,PAPER_CHECK_OUTPUT_VERSION))
            attempts=tuple((await session.scalars(select(ModelRun).where(ModelRun.check_run_id==run.id,
                ModelRun.paper_submission_id==paper_id).order_by(ModelRun.attempt_no))).all())
            if any(x.request_fingerprint!=compiled.request_fingerprint for x in attempts):
                raise PaperCheckingExecutionError("paper_request_conflict")
            if attempts and attempts[-1].status=="running":
                raise PaperCheckingExecutionError("paper_check_in_progress")
            if attempts and attempts[-1].status=="succeeded":
                raise PaperCheckingExecutionError("paper_result_finalize_incomplete")
            if len(attempts)>=3: raise PaperCheckingExecutionError("paper_attempt_budget_exhausted")
            attempt=ModelRun(check_run_id=run.id,assessment_item_id=None,remediation_plan_item_id=None,
                paper_submission_id=paper_id,prompt_version_id=prompt.id,check_result_id=None,
                provider_id=provider_id,model_id=model_id,settings_snapshot={"temperature":"0","max_output_tokens":8192},
                request_fingerprint=compiled.request_fingerprint,attempt_no=len(attempts)+1,timeout_ms=30000,status="running")
            session.add(attempt); await session.flush()
            session.add(CheckerEvent(check_run_id=run.id,event_type="model_attempt",details={"model_run_id":str(attempt.id),"attempt_no":attempt.attempt_no,"source":"paper"}))
            rows=(await session.scalars(select(InputArtifact).where(InputArtifact.id.in_(compiled.artifact_ids.values())))).all()
            by_id={x.id:x for x in rows}; artifacts={token:by_id.get(aid) for token,aid in compiled.artifact_ids.items()}
            if any(x is None for x in artifacts.values()): raise PaperCheckingExecutionError("paper_render_missing")
            return PreparedExecution(compiled,attempt.id,paper_id,artifacts)

    async def fail(self,prepared,code,response):
        async with self.session_factory() as session:
          async with session.begin():
            attempt=await session.get(ModelRun,prepared.model_run_id)
            if attempt and attempt.status=="running":
                attempt.status="invalid" if code=="semantic_invalid" else "failed"; attempt.error_code=code
                attempt.finished_at=await session.scalar(select(func.clock_timestamp()))
            run=await session.scalar(select(CheckRun).where(CheckRun.id==attempt.check_run_id).with_for_update())
            retryable=code!="semantic_invalid" and attempt.attempt_no<3
            if run.status=="running" and not retryable:
                await CheckingRepository(session).transition_run(run.id,run.row_version,
                    "failed_terminal" if code=="semantic_invalid" else "failed_retryable",failure_code=code)
            return retryable

    async def succeed(self,prepared,response,telemetry):
        async with self.session_factory() as session:
          async with session.begin():
            attempt=await session.scalar(select(ModelRun).where(ModelRun.id==prepared.model_run_id).with_for_update())
            run=await session.scalar(select(CheckRun).where(CheckRun.id==attempt.check_run_id).with_for_update())
            prior=await session.scalar(select(PaperAIRevision).where(PaperAIRevision.check_run_id==run.id))
            if prior is not None: return await self._result(session,run.id)
            payload=response.model_dump(mode="json")
            attempt.status="succeeded"; attempt.finished_at=await session.scalar(select(func.clock_timestamp()))
            attempt.provider_request_id=telemetry.provider_request_id; attempt.latency_ms=telemetry.latency_ms
            attempt.input_tokens=telemetry.input_tokens; attempt.output_tokens=telemetry.output_tokens; attempt.cached_tokens=telemetry.cached_tokens
            attempt.raw_output=json.dumps(payload,separators=(",",":")); attempt.validated_output=payload
            policy=run.input_snapshot["grading_policy"]
            revision=PaperAIRevision(check_run_id=run.id,paper_submission_id=prepared.paper_submission_id,
                model_run_id=attempt.id,response_schema_version=response.schema_version,
                prompt_version=PAPER_CHECK_PROMPT_VERSION,prompt_fingerprint=PAPER_CHECK_PROMPT_HASH,
                grading_policy_revision=policy["revision"],grading_policy_fingerprint=policy["fingerprint"],
                validated_response=payload,summary_draft=response.summary_draft,
                overall_confidence=response.overall_confidence,requires_human_review=True)
            session.add(revision); await session.flush()
            for item in response.items:
                assessment_item_id,task_version_id=prepared.compiled.item_ids[item.item_token]
                result=CheckResult(check_run_id=run.id,assessment_item_id=assessment_item_id,
                    remediation_plan_item_id=None,task_version_id=task_version_id,checker_type="paper_vision",
                    checker_version=PAPER_CHECK_PROMPT_VERSION,schema_version=response.schema_version,
                    result_status=item.status,reason_code="paper_vision_result",score_suggested=item.rubric_score,
                    max_score=item.rubric_max_score,confidence=item.confidence,
                    confidence_policy_version="paper_ai_v1",confidence_details={"effective":format(item.confidence,".4f")},
                    summary=(item.findings[0].short_explanation if item.findings else item.status),
                    needs_human_review=True,review_reason="paper_ai_review_required",
                    validated_result=item.model_dump(mode="json"))
                session.add(result); await session.flush()
                for f in item.findings:
                    finding=CheckFinding(check_result_id=result.id,finding_type="general",severity={"info":"info","warning":"minor","error":"major"}[f.severity],
                        confidence=f.confidence,evidence={"finding_token":f.finding_token,"page_token":f.page_token,"region":f.region.model_dump(mode="json")})
                    session.add(finding); await session.flush()
                    session.add(PaperCheckFindingRegion(check_finding_id=finding.id,paper_ai_revision_id=revision.id,
                        scan_page_id=prepared.compiled.page_ids[f.page_token],finding_token=f.finding_token,
                        provider_category=f.category,short_explanation=f.short_explanation,detailed_explanation=f.detailed_explanation,
                        region=f.region.model_dump(mode="json"),coordinate_space_version="normalized_upright_v1",
                        requires_human_review=f.requires_human_review))
                session.add(CheckerEvent(check_run_id=run.id,check_result_id=result.id,assessment_item_id=assessment_item_id,
                    event_type="result_recorded",details={"checker_type":"paper_vision","result_status":item.status}))
            await session.flush(); now=await session.scalar(select(func.clock_timestamp()))
            run.status="completed_with_review_required"; run.finished_at=now; run.row_version+=1
            session.add(CheckerEvent(check_run_id=run.id,event_type="run_transition",from_status="running",
                to_status="completed_with_review_required",details={"item_count":len(response.items),"review_required":True}))
            await session.flush(); return await self._result(session,run.id)

    async def read(self,run_id):
        async with self.session_factory() as session: return await self._result(session,run_id)

    async def _result(self,session,run_id):
        run=await session.get(CheckRun,run_id); revision=await session.scalar(select(PaperAIRevision).where(PaperAIRevision.check_run_id==run_id))
        results=tuple((await session.scalars(select(CheckResult).where(CheckResult.check_run_id==run_id).order_by(CheckResult.created_at,CheckResult.id))).all())
        findings=[]
        for result in results:
            pairs=(await session.execute(select(CheckFinding,PaperCheckFindingRegion).join(PaperCheckFindingRegion,PaperCheckFindingRegion.check_finding_id==CheckFinding.id).where(CheckFinding.check_result_id==result.id))).all()
            findings.extend({"check_result_id":result.id,"finding_token":region.finding_token,"category":region.provider_category,"severity":finding.severity,"confidence":finding.confidence,"page_id":region.scan_page_id,"region":region.region,"short_explanation":region.short_explanation,"detailed_explanation":region.detailed_explanation} for finding,region in pairs)
        return {"check_run_id":run_id,"status":run.status,"ai_revision_id":revision.id if revision else None,
            "summary_draft":revision.summary_draft if revision else None,"overall_confidence":revision.overall_confidence if revision else None,
            "requires_human_review":revision.requires_human_review if revision else None,
            "items":[{"check_result_id":x.id,"assessment_item_id":x.assessment_item_id,"status":x.result_status,"suggested_score":x.score_suggested,"max_score":x.max_score,"confidence":x.confidence,"needs_human_review":x.needs_human_review} for x in results],"findings":findings}
