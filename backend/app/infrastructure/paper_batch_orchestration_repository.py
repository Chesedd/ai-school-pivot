"""Batch row locks serialize transitions with new paper checking attempts."""

from dataclasses import replace

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.application.paper_batch_orchestration import (
    PaperBatchError,
    derive_progress,
    effective_runs,
)
from app.application.scan_checking_contracts import (
    AssessmentScanBatchState,
    validate_batch_transition,
)
from app.infrastructure.assessment_models import Student
from app.infrastructure.checking_models import CheckRun
from app.infrastructure.paper_checking_review_models import PaperCheckReview
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
        batch = await self.session.scalar(
            query.execution_options(populate_existing=True)
        )
        if batch is None:
            raise PaperBatchError("scan_batch_not_found", 404)
        return batch

    async def progress(self, batch):
        manual = aliased(PaperCheckReview)
        # One statement gives all authoritative rows a consistent MVCC snapshot.
        rows = (
            await self.session.execute(
                select(
                    AssessmentScanBatch.status,
                    PaperSubmission.id,
                    CheckRun.attempt_no,
                    CheckRun.status,
                    PaperResultPublicationState.active_publication_id,
                    PaperCheckReview.state,
                    CheckRun.id,
                    PaperSubmission.assignment_participant_id,
                    Student.display_name,
                    PaperSubmission.assigned_variant_id,
                    PaperCheckReview.id,
                    PaperCheckReview.latest_approved_revision_id,
                    manual.state,
                    manual.id,
                    manual.latest_approved_revision_id,
                )
                .select_from(AssessmentScanBatch)
                .outerjoin(
                    PaperSubmission, PaperSubmission.batch_id == AssessmentScanBatch.id
                )
                .outerjoin(CheckRun, CheckRun.paper_submission_id == PaperSubmission.id)
                .outerjoin(Student, Student.id == PaperSubmission.student_id)
                .outerjoin(
                    PaperResultPublicationState,
                    PaperResultPublicationState.paper_submission_id
                    == PaperSubmission.id,
                )
                .outerjoin(
                    PaperCheckReview,
                    (PaperCheckReview.paper_submission_id == PaperSubmission.id)
                    & (PaperCheckReview.check_run_id == CheckRun.id),
                )
                .outerjoin(
                    manual,
                    (manual.paper_submission_id == PaperSubmission.id)
                    & (manual.source_kind == "manual"),
                )
                .where(AssessmentScanBatch.id == batch.id)
            )
        ).all()
        progress = derive_progress(
            batch.id,
            rows[0][0],
            (r[1] for r in rows if r[1] is not None),
            ((r[1], r[2], r[3]) for r in rows if r[2] is not None),
            {r[1] for r in rows if r[4] is not None},
            ((r[1], r[12]) for r in rows if r[12] is not None),
        )
        latest = effective_runs(
            (r[1] for r in rows),
            ((r[1], r[2], r) for r in rows if r[2] is not None),
        )
        papers = {r[1]: r for r in rows if r[1] is not None}
        manual_forbidden = {
            r[1]
            for r in rows
            if r[10]
            or r[3]
            in {"pending", "running", "completed", "completed_with_review_required"}
        }
        entries = []
        for paper_id in sorted(papers, key=str):
            r = latest[paper_id][2] if paper_id in latest else papers[paper_id]
            status = r[3] or "not_started"
            if r[12]:
                status = (
                    "manual_approved" if r[12] == "approved" else "manual_reviewing"
                )
            review_id, review_state, approved = (
                (r[13], r[12], r[14]) if r[12] else (r[10], r[5], r[11])
            )
            # Manual lineage takes precedence; no historical AI pointer is exposed.
            ai_available = not r[12] and status == "completed_with_review_required"
            can_new = progress.batch_status in {
                "ready_for_checking",
                "checking",
                "checking_completed",
            }
            entries.append(
                {
                    "paper_submission_id": paper_id,
                    "assignment_participant_id": r[7],
                    "display_name": r[8],
                    "assigned_variant_id": r[9],
                    "checking_state": status,
                    "effective_check_run_id": None if r[12] else r[6],
                    "ai_review_available": ai_available,
                    "manual_review_available": bool(r[12])
                    or (
                        can_new
                        and paper_id not in manual_forbidden
                        and status
                        in {"not_started", "failed_retryable", "failed_terminal"}
                    ),
                    "review_id": review_id,
                    "review_state": review_state,
                    "latest_approved_revision_id": approved,
                    "can_retry": can_new
                    and not r[12]
                    and status in {"failed_retryable", "failed_terminal"},
                    "can_execute": not r[12] and status == "pending",
                }
            )
        return replace(progress, papers=tuple(entries))

    async def schedule_initial_runs(self, batch, actor_id):
        """Caller holds the batch lock shared with intake/manual review creation."""
        from app.application.checking import CreatePaperRunCommand
        from app.application.checking_intake import CheckingIntakeError, sha256_hex
        from app.application.checking_routing import ROUTING_CONTRACT_VERSION
        from app.application.paper_checking_intake import (
            PAPER_HANDOFF_VERSION,
            PAPER_PROMPT_POLICY_VERSION,
            PAPER_SNAPSHOT_SCHEMA_VERSION,
            PaperCheckingIntakeError,
            PaperCheckingIntakeRequest,
            build_paper_snapshot,
            canonical_paper_run_request,
        )
        from app.infrastructure.paper_checking_repository import (
            SQLAlchemyPaperCheckingIntakeUnitOfWork,
        )

        progress = await self.progress(batch)
        uow = SQLAlchemyPaperCheckingIntakeUnitOfWork(None)
        uow.session, uow.actor_id = self.session, actor_id
        for paper in progress.papers:
            if paper["checking_state"] != "not_started":
                continue
            paper_id = paper["paper_submission_id"]
            try:
                handoff = await uow.load_locked_handoff(paper_id)
                methods = await uow.load_methodologies(
                    tuple(x.task_version_id for x in handoff.items)
                )
                snapshot = build_paper_snapshot(handoff, methods)
            except (CheckingIntakeError, PaperCheckingIntakeError):
                raise PaperBatchError("paper_checking_input_missing") from None
            fingerprint = sha256_hex(snapshot)
            request = PaperCheckingIntakeRequest(
                paper_id,
                f"batch-initial:{paper_id}",
                ROUTING_CONTRACT_VERSION,
                "paper_vision_v1",
                "paper_grading_policy.v1",
                PAPER_PROMPT_POLICY_VERSION,
            )
            await uow.create_paper_run(
                CreatePaperRunCommand(
                    paper_id,
                    request.request_key,
                    sha256_hex(canonical_paper_run_request(request, fingerprint)),
                    PAPER_HANDOFF_VERSION,
                    snapshot,
                    fingerprint,
                    PAPER_SNAPSHOT_SCHEMA_VERSION,
                    request.routing_version,
                    request.checker_set_version,
                    request.threshold_policy_version,
                    request.prompt_model_policy_version,
                )
            )

    async def validate_prerequisites(self, batch):
        papers = (
            await self.session.execute(
                select(
                    PaperSubmission.assigned_variant_id,
                    PaperSubmission.grouping_revision_id,
                ).where(PaperSubmission.batch_id == batch.id)
            )
        ).all()
        if not papers:
            raise PaperBatchError("paper_batch_has_no_papers")
        policies = set(
            (
                await self.session.scalars(
                    select(ScanGradingPolicyRevision.assessment_variant_id).where(
                        ScanGradingPolicyRevision.batch_id == batch.id
                    )
                )
            ).all()
        )
        if policies != {r[0] for r in papers}:
            raise PaperBatchError("paper_grading_policy_incomplete")
        confirmed = set(
            (
                await self.session.scalars(
                    select(ScanGroupingRevision.id).where(
                        ScanGroupingRevision.batch_id == batch.id,
                        ScanGroupingRevision.status == "confirmed",
                    )
                )
            ).all()
        )
        if not {r[1] for r in papers}.issubset(confirmed):
            raise PaperBatchError("paper_batch_grouping_not_confirmed")

    async def begin_attempt(self, batch, *, actor_id=None):
        if batch.status == "completed":
            raise PaperBatchError("paper_batch_completed")
        if batch.status not in {"ready_for_checking", "checking", "checking_completed"}:
            raise PaperBatchError("paper_batch_not_ready_for_checking")
        if batch.status != "checking":
            await self.validate_prerequisites(batch)
            await self.transition(
                batch,
                "checking",
                actor_id or batch.created_by_user_id,
                await self.progress(batch),
                new_attempt=True,
            )

    async def transition(self, batch, target, actor_id, progress, *, new_attempt=False):
        current = batch.status
        validate_batch_transition(
            AssessmentScanBatchState(current),
            AssessmentScanBatchState(target),
            new_attempt=new_attempt,
        )
        event = {
            "checking": "batch.checking_reopened"
            if current == "checking_completed"
            else "batch.checking_started",
            "checking_completed": "batch.checking_completed",
            "completed": "batch.completed",
        }[target]
        batch.status = target
        batch.row_version += 1
        batch.updated_at = await self.session.scalar(select(func.clock_timestamp()))
        self.session.add(
            ScanCheckingEvent(
                batch_id=batch.id,
                aggregate_type="batch",
                aggregate_id=batch.id,
                event_type=event,
                actor_user_id=actor_id,
                details={
                    "paper_count": progress.paper_count,
                    "settled_count": progress.settled_count,
                    "failed_terminal_count": progress.failed_terminal,
                    "active_publication_count": progress.active_publication_count,
                },
            )
        )
        await self.session.flush()
