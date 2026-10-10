"""Batch row locks serialize transitions with new paper checking attempts."""
from sqlalchemy import func, select

from app.application.paper_batch_orchestration import PaperBatchError, derive_progress
from app.application.scan_checking_contracts import (
    AssessmentScanBatchState,
    validate_batch_transition,
)
from app.infrastructure.checking_models import CheckRun
from app.infrastructure.paper_result_publication_models import (
    PaperResultPublicationState,
)
from app.infrastructure.scan_checking_models import (
    AssessmentScanBatch,
    PaperSubmission,
    ScanCheckingEvent,
    ScanGradingPolicyRevision,
    ScanGroupingRevision,
)


class PaperBatchOrchestrationRepository:
    def __init__(self, session):
        self.session = session

    async def load(self, batch_id, *, lock=False):
        query = select(AssessmentScanBatch).where(AssessmentScanBatch.id == batch_id)
        if lock:
            query = query.with_for_update()
        # Refresh a row possibly loaded before waiting for a concurrent transition.
        batch = await self.session.scalar(query.execution_options(populate_existing=True))
        if batch is None:
            raise PaperBatchError("scan_batch_not_found", 404)
        return batch

    async def progress(self, batch):
        # One statement gives all authoritative rows a consistent MVCC snapshot.
        rows = (await self.session.execute(
            select(AssessmentScanBatch.status, PaperSubmission.id, CheckRun.attempt_no, CheckRun.status,
                   PaperResultPublicationState.active_publication_id)
            .select_from(AssessmentScanBatch)
            .outerjoin(PaperSubmission, PaperSubmission.batch_id == AssessmentScanBatch.id)
            .outerjoin(CheckRun, CheckRun.paper_submission_id == PaperSubmission.id)
            .outerjoin(PaperResultPublicationState,
                       PaperResultPublicationState.paper_submission_id == PaperSubmission.id)
            .where(AssessmentScanBatch.id == batch.id)
        )).all()
        return derive_progress(
            batch.id, rows[0][0], (r[1] for r in rows if r[1] is not None),
            ((r[1], r[2], r[3]) for r in rows if r[2] is not None),
            {r[1] for r in rows if r[4] is not None},
        )

    async def validate_prerequisites(self, batch):
        papers = (await self.session.execute(select(
            PaperSubmission.assigned_variant_id, PaperSubmission.grouping_revision_id
        ).where(PaperSubmission.batch_id == batch.id))).all()
        if not papers:
            raise PaperBatchError("paper_batch_has_no_papers")
        policies = set((await self.session.scalars(select(
            ScanGradingPolicyRevision.assessment_variant_id
        ).where(ScanGradingPolicyRevision.batch_id == batch.id))).all())
        if policies != {r[0] for r in papers}:
            raise PaperBatchError("paper_grading_policy_incomplete")
        confirmed = set((await self.session.scalars(select(ScanGroupingRevision.id).where(
            ScanGroupingRevision.batch_id == batch.id,
            ScanGroupingRevision.status == "confirmed",
        ))).all())
        if not {r[1] for r in papers}.issubset(confirmed):
            raise PaperBatchError("paper_batch_grouping_not_confirmed")

    async def begin_attempt(self, batch, *, actor_id=None):
        if batch.status == "completed":
            raise PaperBatchError("paper_batch_completed")
        if batch.status not in {"ready_for_checking", "checking", "checking_completed"}:
            raise PaperBatchError("paper_batch_not_ready_for_checking")
        if batch.status != "checking":
            await self.validate_prerequisites(batch)
            await self.transition(batch, "checking", actor_id or batch.created_by_user_id,
                                  await self.progress(batch), new_attempt=True)

    async def transition(self, batch, target, actor_id, progress, *, new_attempt=False):
        current = batch.status
        validate_batch_transition(AssessmentScanBatchState(current),
                                  AssessmentScanBatchState(target), new_attempt=new_attempt)
        event = {
            "checking": "batch.checking_reopened" if current == "checking_completed"
                        else "batch.checking_started",
            "checking_completed": "batch.checking_completed",
            "completed": "batch.completed",
        }[target]
        batch.status = target
        batch.row_version += 1
        batch.updated_at = await self.session.scalar(select(func.clock_timestamp()))
        self.session.add(ScanCheckingEvent(
            batch_id=batch.id, aggregate_type="batch", aggregate_id=batch.id,
            event_type=event, actor_user_id=actor_id,
            details={"paper_count": progress.paper_count, "settled_count": progress.settled_count,
                     "failed_terminal_count": progress.failed_terminal,
                     "active_publication_count": progress.active_publication_count},
        ))
        await self.session.flush()
