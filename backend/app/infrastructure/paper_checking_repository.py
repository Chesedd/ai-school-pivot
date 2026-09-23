"""PostgreSQL adapters for paper policy freezing and checking intake."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.paper_checking_intake import (FrozenPaperPolicy, PaperCheckingHandoff,
    PaperCheckingItem, PaperCheckingPage, PaperGradingPolicyIncomplete,
    PAPER_POLICY_COMPILER_VERSION, PAPER_PROMPT_POLICY_VERSION)
from app.application.checking_intake import InvalidCheckingInput
from app.application.scan_grading_policy import ScanGradingPolicy
from app.infrastructure.assessment_models import AssessmentItem, AssessmentVariant, Assignment
from app.infrastructure.authoring_models import InputArtifact
from app.infrastructure.checking_models import CheckRun
from app.infrastructure.checking_repository import CheckingRepository
from app.infrastructure.checking_intake_repository import SQLAlchemyCheckingIntakeUnitOfWork
from app.infrastructure.scan_checking_models import (AssessmentScanBatch, PaperSubmission,
    PaperSubmissionPage, ScanPage, ScanGradingPolicyRevision)

class SQLAlchemyPaperCheckingIntakeUnitOfWork(SQLAlchemyCheckingIntakeUnitOfWork):
    async def load_locked_handoff(self, paper_submission_id: UUID) -> PaperCheckingHandoff:
        paper=await self.session.scalar(select(PaperSubmission).where(
            PaperSubmission.id==paper_submission_id).with_for_update())
        if paper is None: raise InvalidCheckingInput("paper_submission_not_found")
        batch=await self.session.scalar(select(AssessmentScanBatch).where(
            AssessmentScanBatch.id==paper.batch_id).with_for_update())
        if batch.status not in {"ready_for_checking","checking"}:
            raise InvalidCheckingInput("paper_batch_not_checkable")
        represented=set((await self.session.scalars(select(PaperSubmission.assigned_variant_id)
            .where(PaperSubmission.batch_id==batch.id))).all())
        policies=(await self.session.scalars(select(ScanGradingPolicyRevision).where(
            ScanGradingPolicyRevision.batch_id==batch.id).order_by(
            ScanGradingPolicyRevision.assessment_variant_id,
            ScanGradingPolicyRevision.revision.desc()))).all()
        current={}
        for p in policies: current.setdefault(p.assessment_variant_id,p)
        if set(current) != represented: raise PaperGradingPolicyIncomplete("paper_grading_policy_incomplete")
        policy=current[paper.assigned_variant_id]
        rows=(await self.session.execute(select(PaperSubmissionPage,ScanPage,InputArtifact)
            .join(ScanPage,ScanPage.id==PaperSubmissionPage.scan_page_id)
            .join(InputArtifact,InputArtifact.id==ScanPage.derived_render_artifact_id)
            .where(PaperSubmissionPage.paper_submission_id==paper.id)
            .order_by(PaperSubmissionPage.page_order))).all()
        pages=[]
        for membership,page,artifact in rows:
            if page.status!="ready" or artifact.mime_type!="image/png" or artifact.content_hash_sha256!=page.content_fingerprint:
                raise InvalidCheckingInput("invalid derived paper render")
            pages.append(PaperCheckingPage(page.id,membership.page_order,page.width_px,page.height_px,
                page.coordinate_space_version,page.content_fingerprint,artifact.id))
        item_rows=(await self.session.execute(select(AssessmentItem,).where(
            AssessmentItem.variant_id==paper.assigned_variant_id).order_by(
            AssessmentItem.position,AssessmentItem.id))).scalars().all()
        # Answer format is checked against the same historical methodology loaded next.
        from app.infrastructure.models import TaskVersion
        versions={v.id:v for v in (await self.session.scalars(select(TaskVersion).where(
            TaskVersion.id.in_([x.task_version_id for x in item_rows])))).all()}
        items=tuple(PaperCheckingItem(x.id,x.task_version_id,x.position,x.points,
            versions[x.task_version_id].answer_format) for x in item_rows)
        return PaperCheckingHandoff(paper.id,paper.batch_id,paper.assignment_id,
            paper.assigned_variant_id,paper.grouping_revision_id,tuple(pages),items,
            FrozenPaperPolicy(policy.id,policy.revision,policy.policy_schema_version,
                policy.policy_fingerprint,dict(policy.policy_json)))

    async def create_paper_run(self,command):
        return await CheckingRepository(self.session).create_paper_run(command)

    async def mark_checking_started(self,batch_id):
        batch=await self.session.get(AssessmentScanBatch,batch_id)
        if batch.status=="ready_for_checking": batch.status="checking"; batch.row_version+=1

class SQLAlchemyPaperCheckingIntakeUnitOfWorkFactory:
    def __init__(self,factory): self.factory=factory
    def __call__(self): return SQLAlchemyPaperCheckingIntakeUnitOfWork(self.factory)

class PaperGradingPolicyRepository:
    def __init__(self,session: AsyncSession): self.session=session
    async def freeze(self,batch_id:UUID,variant_id:UUID,supplied:ScanGradingPolicy,actor_id:UUID):
        batch=await self.session.scalar(select(AssessmentScanBatch).where(
            AssessmentScanBatch.id==batch_id).with_for_update())
        if batch is None: raise InvalidCheckingInput("scan_batch_not_found")
        if batch.status!="ready_for_checking": raise InvalidCheckingInput("paper_grading_policy_locked")
        assignment=await self.session.get(Assignment,batch.assignment_id)
        variant=await self.session.get(AssessmentVariant,variant_id)
        if variant is None or variant.assessment_id!=assignment.assessment_id:
            raise InvalidCheckingInput("foreign_assessment_variant")
        represented=await self.session.scalar(select(PaperSubmission.id).where(
            PaperSubmission.batch_id==batch_id,PaperSubmission.assigned_variant_id==variant_id).limit(1))
        if represented is None: raise InvalidCheckingInput("variant_not_represented")
        canonical=(await self.session.scalars(select(AssessmentItem).where(
            AssessmentItem.variant_id==variant_id))).all()
        maxima={x.id:x.points for x in canonical}
        proposed={x.assessment_item_id:x.max_score for x in supplied.assessment_items}
        if proposed!=maxima: raise InvalidCheckingInput("paper_grading_policy_item_coverage")
        if supplied.original_teacher_instruction != batch.instruction_text:
            raise InvalidCheckingInput("paper_grading_policy_instruction_mismatch")
        if (supplied.compiler_version != PAPER_POLICY_COMPILER_VERSION or
            supplied.prompt_policy_version != PAPER_PROMPT_POLICY_VERSION):
            raise InvalidCheckingInput("paper_grading_policy_version_mismatch")
        final=supplied.model_copy(update={"original_teacher_instruction":batch.instruction_text,
            "compiler_version":PAPER_POLICY_COMPILER_VERSION,
            "prompt_policy_version":PAPER_PROMPT_POLICY_VERSION})
        latest=await self.session.scalar(select(ScanGradingPolicyRevision).where(
            ScanGradingPolicyRevision.batch_id==batch_id,
            ScanGradingPolicyRevision.assessment_variant_id==variant_id).order_by(
            ScanGradingPolicyRevision.revision.desc()).limit(1))
        if latest and latest.policy_fingerprint==final.fingerprint: return latest
        if await self.session.scalar(select(CheckRun.id).join(PaperSubmission,
            PaperSubmission.id==CheckRun.paper_submission_id).where(PaperSubmission.batch_id==batch_id).limit(1)):
            raise InvalidCheckingInput("paper_grading_policy_locked")
        row=ScanGradingPolicyRevision(batch_id=batch_id,assessment_variant_id=variant_id,
            revision=(latest.revision+1 if latest else 1),policy_schema_version=final.schema_version,
            compiler_version=final.compiler_version,prompt_policy_version=final.prompt_policy_version,
            policy_json=final.model_dump(mode="json"),policy_fingerprint=final.fingerprint,
            created_by_user_id=actor_id,supersedes_policy_id=latest.id if latest else None)
        self.session.add(row); await self.session.flush(); return row
    async def list(self,batch_id):
        return (await self.session.scalars(select(ScanGradingPolicyRevision).where(
            ScanGradingPolicyRevision.batch_id==batch_id).order_by(
            ScanGradingPolicyRevision.assessment_variant_id,ScanGradingPolicyRevision.revision))).all()
