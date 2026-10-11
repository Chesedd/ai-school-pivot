"""Paper source adapter for the existing teacher-controlled remediation plans."""

from sqlalchemy import select

from app.application.paper_remediation import published_signals, validate_signals
from app.application.remediation import RemediationError, fingerprint, validate_item_ids
from app.infrastructure.assessment_models import (
    AssessmentItem,
    AssessmentVariant,
    Assignment,
    AssignmentParticipant,
    Student,
)
from app.infrastructure.checking_models import CheckRun
from app.infrastructure.classroom_repository import SQLAlchemyClassroomAccessRepository
from app.infrastructure.paper_checking_review_models import PaperCheckReviewRevision
from app.infrastructure.paper_result_publication_models import (
    PaperResultPublication,
    PaperResultPublicationState,
)
from app.infrastructure.remediation_models import (
    RemediationEvent,
    RemediationPlan,
    RemediationPlanItem,
    RemediationPlanPaperFindingSignal,
    RemediationPlanPaperItemSignal,
)
from app.infrastructure.scan_checking_models import AssessmentScanBatch, PaperSubmission


class PaperRemediationRepository:
    def __init__(self, session):
        self.s = session

    async def source(self, actor, admin, paper_id, publication_id=None, *, lock=False):
        paper = (
            await self.s.scalar(
                select(PaperSubmission)
                .where(PaperSubmission.id == paper_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if lock
            else await self.s.get(PaperSubmission, paper_id)
        )
        batch = await self.s.get(AssessmentScanBatch, paper.batch_id) if paper else None
        if not batch or not await SQLAlchemyClassroomAccessRepository(
            self.s
        ).can_access_class(batch.class_group_id, actor, admin):
            raise RemediationError("remediation_source_not_found", 404)
        state = await self.s.scalar(
            select(PaperResultPublicationState)
            .where(PaperResultPublicationState.paper_submission_id == paper_id)
            .execution_options(populate_existing=True)
        )
        if publication_id is not None and (
            not state or state.active_publication_id != publication_id
        ):
            raise RemediationError("remediation_source_publication_stale")
        if not state:
            return paper, None, None, set(), None, None
        publication = await self.s.get(
            PaperResultPublication, state.active_publication_id
        )
        if publication is None or publication.paper_submission_id != paper.id:
            raise RemediationError("remediation_source_publication_invalid")
        snapshot, eligible = published_signals(publication, paper.id)
        assignment = await self.s.get(Assignment, paper.assignment_id)
        participant = await self.s.get(
            AssignmentParticipant, paper.assignment_participant_id
        )
        student = await self.s.get(Student, paper.student_id)
        variant = await self.s.get(AssessmentVariant, paper.assigned_variant_id)
        revision = await self.s.get(
            PaperCheckReviewRevision, publication.approved_review_revision_id
        )
        run = await self.s.get(CheckRun, revision.check_run_id) if revision else None
        if (
            not assignment
            or not participant
            or not student
            or not variant
            or not revision
            or not run
            or assignment.id != batch.assignment_id
            or assignment.class_group_id != batch.class_group_id
            or participant.assignment_id != assignment.id
            or participant.student_id != student.id
            or variant.assessment_id != assignment.assessment_id
            or revision.paper_submission_id != paper.id
            or revision.review_id != publication.review_id
            or revision.revision_kind != "approved"
            or run.paper_submission_id != paper.id
        ):
            raise RemediationError("remediation_source_publication_invalid")
        rows = (
            await self.s.scalars(
                select(AssessmentItem).where(
                    AssessmentItem.variant_id == paper.assigned_variant_id
                )
            )
        ).all()
        if {x.assessment_item_id for x in snapshot.items} != {
            x.id for x in rows
        } or any(
            x.max_score != next(r.points for r in rows if r.id == x.assessment_item_id)
            for x in snapshot.items
        ):
            raise RemediationError("remediation_source_publication_invalid")
        if student.archived_at:
            raise RemediationError("remediation_student_archived")
        if student.class_group_id != assignment.class_group_id:
            raise RemediationError("remediation_student_moved")
        return paper, publication, snapshot, eligible, assignment, run

    async def eligibility(self, actor, admin, paper_id):
        paper, pub, _, eligible, _, _ = await self.source(actor, admin, paper_id)
        return {
            "paper_submission_id": paper.id,
            "active_publication_id": pub.id if pub else None,
            "eligible": bool(eligible),
            "eligible_item_count": len(eligible),
            "eligible_assessment_item_ids": sorted(eligible, key=str),
            "reason": None
            if eligible
            else ("no_active_publication" if pub is None else "no_weak_items"),
        }

    async def selected(
        self,
        actor,
        admin,
        paper_id,
        publication_id,
        item_ids,
        finding_ids,
        *,
        lock=True,
    ):
        source = await self.source(actor, admin, paper_id, publication_id, lock=lock)
        validate_signals(source[2], source[3], item_ids, finding_ids)
        return source

    def signals(self, plan, item_ids, finding_ids):
        self.s.add_all(
            [
                RemediationPlanPaperItemSignal(
                    remediation_plan_id=plan.id,
                    source_paper_publication_id=plan.source_paper_publication_id,
                    assessment_item_id=x,
                )
                for x in item_ids
            ]
        )
        self.s.add_all(
            [
                RemediationPlanPaperFindingSignal(
                    remediation_plan_id=plan.id,
                    source_paper_publication_id=plan.source_paper_publication_id,
                    publication_finding_id=x,
                )
                for x in finding_ids
            ]
        )

    async def create(self, actor, admin, paper_id, payload, remediation):
        validate_item_ids(payload.items)
        intent = {
            **payload.model_dump(mode="json", exclude={"creation_key"}),
            "paper_submission_id": str(paper_id),
        }
        fp = fingerprint(intent)
        # Serialize creation with publication replacement; same-key retries still
        # return historical intent, just as digital creation retries do.
        await self.s.scalar(
            select(PaperSubmission)
            .where(PaperSubmission.id == paper_id)
            .with_for_update()
        )
        old = await self.s.scalar(
            select(RemediationPlan).where(
                RemediationPlan.owner_user_id == actor,
                RemediationPlan.creation_key == payload.creation_key,
            )
        )
        if old:
            await remediation.owned(old.id, actor, admin)
            if old.creation_fingerprint != fp:
                raise RemediationError("remediation_idempotency_conflict")
            return await remediation.view(old)
        paper, pub, _, _, assignment, run = await self.selected(
            actor,
            admin,
            paper_id,
            payload.source_paper_publication_id,
            payload.source_assessment_item_ids,
            payload.publication_finding_ids,
        )
        plan = RemediationPlan(
            owner_user_id=actor,
            student_id=paper.student_id,
            class_group_id=assignment.class_group_id,
            source_assignment_id=assignment.id,
            source_assignment_participant_id=paper.assignment_participant_id,
            source_submission_id=None,
            source_check_run_id=run.id,
            source_paper_submission_id=paper.id,
            source_paper_publication_id=pub.id,
            title=payload.title,
            instructions=payload.instructions,
            due_at=payload.due_at,
            creation_key=payload.creation_key,
            creation_fingerprint=fp,
        )
        self.s.add(plan)
        await self.s.flush()
        self.signals(
            plan, payload.source_assessment_item_ids, payload.publication_finding_ids
        )
        self.s.add_all(
            [
                RemediationPlanItem(
                    remediation_plan_id=plan.id,
                    position=i,
                    task_version_id=x.task_version_id,
                    selection_source=x.selection_source,
                )
                for i, x in enumerate(payload.items)
            ]
        )
        self.s.add(
            RemediationEvent(
                remediation_plan_id=plan.id,
                event_type="plan_created",
                actor_user_id=actor,
                details={"item_count": len(payload.items), "source_type": "paper"},
            )
        )
        await self.s.flush()
        return await remediation.view(plan)
