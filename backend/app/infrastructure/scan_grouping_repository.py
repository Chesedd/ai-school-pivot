"""PostgreSQL persistence boundary for teacher-owned scan grouping."""

from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import delete, func, select
from app.application.scan_checking_contracts import (
    ConfirmedPaperGroup,
    PageGroupingRevision,
    validate_batch_transition,
    validate_grouping_revision,
    AssessmentScanBatchState,
)
from app.application.scan_grouping import GroupingSeedPage, seed_grouping_pages
from app.infrastructure.assessment_models import AssignmentParticipant, Student
from app.infrastructure.scan_checking_models import (
    AssessmentScanBatch,
    PaperSubmission,
    PaperSubmissionPage,
    ScanBatchArtifact,
    ScanCheckingEvent,
    ScanGroupingEntry,
    ScanGroupingPage,
    ScanGroupingRevision,
    ScanGroupingUnmatchedPage,
    ScanMatchingPageEntry,
    ScanMatchingRun,
    ScanPage,
    ScanPageMatchCandidate,
    ScanPageMatchProposal,
)


class GroupingRepositoryError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class SqlAlchemyScanGroupingRepository:
    def __init__(self, session):
        self.s = session

    async def initialize(self, batch_id, actor_id):
        batch = await self._lock_batch(batch_id)
        if batch is None:
            raise GroupingRepositoryError("scan_batch_not_found")
        existing = await self._current_revision(batch_id, lock=True)
        if (
            existing
            and existing.status == "draft"
            and existing.based_on_matching_revision == batch.matching_revision
        ):
            await self.s.commit()
            return await self.read(batch_id)
        if batch.status != "matching_review_required":
            if existing and existing.status == "confirmed":
                return await self.read(batch_id)
            raise GroupingRepositoryError("grouping_initialization_not_allowed")
        if existing and existing.status == "draft":
            existing.status = "superseded"
            existing.updated_at = datetime.now(timezone.utc)
            await self.s.flush()
        number = (
            await self.s.scalar(
                select(func.coalesce(func.max(ScanGroupingRevision.revision), 0)).where(
                    ScanGroupingRevision.batch_id == batch_id
                )
            )
        ) + 1
        revision = ScanGroupingRevision(
            batch_id=batch_id,
            revision=number,
            based_on_matching_revision=batch.matching_revision,
            created_by_user_id=actor_id,
            status="draft",
        )
        self.s.add(revision)
        await self.s.flush()
        run = await self.s.scalar(
            select(ScanMatchingRun).where(
                ScanMatchingRun.batch_id == batch_id,
                ScanMatchingRun.revision == batch.matching_revision,
                ScanMatchingRun.status == "succeeded",
            )
        )
        if run is None:
            raise GroupingRepositoryError("matching_evidence_not_found")
        rows = (
            await self.s.execute(
                select(ScanMatchingPageEntry, ScanPageMatchProposal)
                .join(
                    ScanPageMatchProposal,
                    (
                        ScanPageMatchProposal.matching_run_id
                        == ScanMatchingPageEntry.matching_run_id
                    )
                    & (
                        ScanPageMatchProposal.scan_page_id
                        == ScanMatchingPageEntry.scan_page_id
                    ),
                )
                .where(ScanMatchingPageEntry.matching_run_id == run.id)
                .order_by(ScanMatchingPageEntry.source_order)
            )
        ).all()
        valid = set(
            (
                await self.s.scalars(
                    select(AssignmentParticipant.id).where(
                        AssignmentParticipant.assignment_id == batch.assignment_id
                    )
                )
            ).all()
        )
        grouped, unresolved = seed_grouping_pages(
            [
                GroupingSeedPage(
                    page_id=page.scan_page_id,
                    source_order=page.source_order,
                    disposition=proposal.disposition,
                    proposed_assignment_participant_id=proposal.proposed_assignment_participant_id,
                    proposed_page_order=proposal.proposed_page_order,
                )
                for page, proposal in rows
            ],
            valid,
        )
        for position, participant in enumerate(sorted(grouped, key=str)):
            entry = ScanGroupingEntry(
                grouping_revision_id=revision.id,
                assignment_participant_id=participant,
                position=position,
            )
            self.s.add(entry)
            await self.s.flush()
            for order, page_id in enumerate(grouped[participant]):
                self.s.add(
                    ScanGroupingPage(
                        grouping_entry_id=entry.id,
                        grouping_revision_id=revision.id,
                        scan_page_id=page_id,
                        page_order=order,
                    )
                )
        for page_id, reason in unresolved:
            self.s.add(
                ScanGroupingUnmatchedPage(
                    grouping_revision_id=revision.id,
                    scan_page_id=page_id,
                    reason_code=reason,
                )
            )
        self._event(
            batch_id,
            "grouping",
            revision.id,
            "grouping.draft_created",
            actor_id,
            {
                "revision": number,
                "group_count": len(grouped),
                "unresolved_count": len(unresolved),
            },
        )
        await self.s.commit()
        return await self.read(batch_id)

    async def assign(
        self,
        batch_id,
        page_id,
        participant_id,
        expected_revision,
        expected_row_version,
        actor_id,
    ):
        try:
            return await self._assign(
                batch_id,
                page_id,
                participant_id,
                expected_revision,
                expected_row_version,
                actor_id,
            )
        except Exception:
            await self.s.rollback()
            raise

    async def _assign(
        self,
        batch_id,
        page_id,
        participant_id,
        expected_revision,
        expected_row_version,
        actor_id,
    ):
        batch, rev = await self._editable(
            batch_id, expected_revision, expected_row_version
        )
        owned = await self.s.scalar(
            select(ScanPage.id)
            .join(ScanBatchArtifact)
            .where(ScanPage.id == page_id, ScanBatchArtifact.batch_id == batch_id)
        )
        if owned is None:
            raise GroupingRepositoryError("grouping_page_not_in_batch")
        entry = None
        if participant_id is not None:
            participant = await self.s.get(AssignmentParticipant, participant_id)
            if participant is None or participant.assignment_id != batch.assignment_id:
                raise GroupingRepositoryError("grouping_participant_not_in_assignment")
            entry = await self.s.scalar(
                select(ScanGroupingEntry).where(
                    ScanGroupingEntry.grouping_revision_id == rev.id,
                    ScanGroupingEntry.assignment_participant_id == participant_id,
                )
            )
            if entry is None:
                pos = await self.s.scalar(
                    select(func.count())
                    .select_from(ScanGroupingEntry)
                    .where(ScanGroupingEntry.grouping_revision_id == rev.id)
                )
                entry = ScanGroupingEntry(
                    grouping_revision_id=rev.id,
                    assignment_participant_id=participant_id,
                    position=pos,
                )
                self.s.add(entry)
                await self.s.flush()
        old = await self.s.scalar(
            select(ScanGroupingPage).where(
                ScanGroupingPage.grouping_revision_id == rev.id,
                ScanGroupingPage.scan_page_id == page_id,
            )
        )
        if old:
            await self.s.delete(old)
            await self.s.flush()
            await self._compact_entry(old.grouping_entry_id, rev.id)
        await self.s.execute(
            delete(ScanGroupingUnmatchedPage).where(
                ScanGroupingUnmatchedPage.grouping_revision_id == rev.id,
                ScanGroupingUnmatchedPage.scan_page_id == page_id,
            )
        )
        if entry:
            order = await self.s.scalar(
                select(func.count())
                .select_from(ScanGroupingPage)
                .where(ScanGroupingPage.grouping_entry_id == entry.id)
            )
            self.s.add(
                ScanGroupingPage(
                    grouping_entry_id=entry.id,
                    grouping_revision_id=rev.id,
                    scan_page_id=page_id,
                    page_order=order,
                )
            )
        else:
            self.s.add(
                ScanGroupingUnmatchedPage(
                    grouping_revision_id=rev.id,
                    scan_page_id=page_id,
                    reason_code="teacher_unresolved",
                )
            )
        self._event(
            batch_id,
            "grouping",
            rev.id,
            "grouping.updated",
            actor_id,
            {"revision": rev.revision, "operation": "assign" if entry else "unassign"},
        )
        await self.s.commit()
        return await self.read(batch_id)

    async def reorder(
        self,
        batch_id,
        participant_id,
        page_ids,
        expected_revision,
        expected_row_version,
        actor_id,
    ):
        try:
            return await self._reorder(
                batch_id,
                participant_id,
                page_ids,
                expected_revision,
                expected_row_version,
                actor_id,
            )
        except Exception:
            await self.s.rollback()
            raise

    async def _reorder(
        self,
        batch_id,
        participant_id,
        page_ids,
        expected_revision,
        expected_row_version,
        actor_id,
    ):
        _, rev = await self._editable(batch_id, expected_revision, expected_row_version)
        entry = await self.s.scalar(
            select(ScanGroupingEntry).where(
                ScanGroupingEntry.grouping_revision_id == rev.id,
                ScanGroupingEntry.assignment_participant_id == participant_id,
            )
        )
        if entry is None:
            raise GroupingRepositoryError("grouping_group_not_found")
        current = (
            await self.s.scalars(
                select(ScanGroupingPage).where(
                    ScanGroupingPage.grouping_entry_id == entry.id
                )
            )
        ).all()
        if len(page_ids) != len(set(page_ids)):
            raise GroupingRepositoryError("grouping_duplicate_page")
        if set(page_ids) != {p.scan_page_id for p in current}:
            raise GroupingRepositoryError("grouping_page_order_mismatch")
        for n, p in enumerate(current):
            p.page_order = -(n + 1)
        await self.s.flush()
        by_id = {p.scan_page_id: p for p in current}
        for n, page_id in enumerate(page_ids):
            by_id[page_id].page_order = n
        self._event(
            batch_id,
            "grouping",
            rev.id,
            "grouping.updated",
            actor_id,
            {"revision": rev.revision, "operation": "reorder"},
        )
        await self.s.commit()
        return await self.read(batch_id)

    async def confirm(
        self, batch_id, expected_revision, expected_row_version, actor_id
    ):
        try:
            batch = await self._lock_batch(batch_id)
            rev = await self._current_revision(batch_id, lock=True)
            if batch is None or rev is None:
                raise GroupingRepositoryError("grouping_not_found")
            if rev.status == "confirmed" and rev.revision == expected_revision:
                await self.s.commit()
                return await self.read(batch_id)
            if (
                rev.revision != expected_revision
                or rev.row_version != expected_row_version
            ):
                raise GroupingRepositoryError("grouping_revision_conflict")
            if rev.status != "draft":
                raise GroupingRepositoryError("grouping_immutable")
            if rev.based_on_matching_revision != batch.matching_revision:
                raise GroupingRepositoryError("grouping_based_on_stale_matching")
            if batch.status != "matching_review_required":
                raise GroupingRepositoryError("grouping_confirmation_not_allowed")
            await self._validate(batch, rev)
            entries = (
                await self.s.scalars(
                    select(ScanGroupingEntry)
                    .where(ScanGroupingEntry.grouping_revision_id == rev.id)
                    .order_by(ScanGroupingEntry.position)
                )
            ).all()
            now = datetime.now(timezone.utc)
            rev.status = "confirmed"
            rev.confirmed_at = now
            rev.confirmed_by_user_id = actor_id
            rev.row_version += 1
            rev.updated_at = now
            submissions = []
            for entry in entries:
                participant = await self.s.get(
                    AssignmentParticipant, entry.assignment_participant_id
                )
                paper = PaperSubmission(
                    batch_id=batch.id,
                    grouping_revision_id=rev.id,
                    assignment_id=batch.assignment_id,
                    assignment_participant_id=participant.id,
                    student_id=participant.student_id,
                    assigned_variant_id=participant.assigned_variant_id,
                    status="ready_for_checking",
                    created_by_user_id=actor_id,
                )
                self.s.add(paper)
                await self.s.flush()
                submissions.append(paper)
                pages = (
                    await self.s.scalars(
                        select(ScanGroupingPage)
                        .where(ScanGroupingPage.grouping_entry_id == entry.id)
                        .order_by(ScanGroupingPage.page_order)
                    )
                ).all()
                for p in pages:
                    self.s.add(
                        PaperSubmissionPage(
                            paper_submission_id=paper.id,
                            scan_page_id=p.scan_page_id,
                            page_order=p.page_order,
                        )
                    )
                self._event(
                    batch.id,
                    "paper_submission",
                    paper.id,
                    "paper_submission.created",
                    actor_id,
                    {"revision": rev.revision, "page_count": len(pages)},
                )
            validate_batch_transition(
                AssessmentScanBatchState(batch.status),
                AssessmentScanBatchState.GROUPING_CONFIRMED,
            )
            batch.status = "grouping_confirmed"
            self._event(
                batch.id,
                "grouping",
                rev.id,
                "grouping.confirmed",
                actor_id,
                {"revision": rev.revision, "paper_submission_count": len(submissions)},
            )
            validate_batch_transition(
                AssessmentScanBatchState(batch.status),
                AssessmentScanBatchState.READY_FOR_CHECKING,
            )
            batch.status = "ready_for_checking"
            batch.row_version += 1
            batch.updated_at = now
            await self.s.commit()
            return await self.read(batch_id)
        except Exception:
            await self.s.rollback()
            raise

    async def read(self, batch_id):
        batch = await self.s.get(AssessmentScanBatch, batch_id)
        rev = await self._current_revision(batch_id)
        if batch is None or rev is None:
            raise GroupingRepositoryError("grouping_not_found")
        run = await self.s.scalar(
            select(ScanMatchingRun).where(
                ScanMatchingRun.batch_id == batch_id,
                ScanMatchingRun.revision == rev.based_on_matching_revision,
            )
        )
        proposals = {
            p.scan_page_id: p
            for p in (
                await self.s.scalars(
                    select(ScanPageMatchProposal).where(
                        ScanPageMatchProposal.matching_run_id == run.id
                    )
                )
            ).all()
        }
        source = {
            p.scan_page_id: p.source_order
            for p in (
                await self.s.scalars(
                    select(ScanMatchingPageEntry).where(
                        ScanMatchingPageEntry.matching_run_id == run.id
                    )
                )
            ).all()
        }
        candidates = {}
        candidate_rows = (
            await self.s.execute(
                select(ScanPageMatchCandidate, AssignmentParticipant, Student)
                .join(
                    AssignmentParticipant,
                    AssignmentParticipant.id
                    == ScanPageMatchCandidate.assignment_participant_id,
                )
                .join(Student, Student.id == AssignmentParticipant.student_id)
                .where(ScanPageMatchCandidate.matching_run_id == run.id)
                .order_by(ScanPageMatchCandidate.rank)
            )
        ).all()
        for c, p, s in candidate_rows:
            candidates.setdefault(c.scan_page_id, []).append(
                {
                    "assignment_participant_id": p.id,
                    "student_id": p.student_id,
                    "display_name": s.display_name,
                    "rank": c.rank,
                }
            )

        def page_view(page_id, order=None):
            p = proposals[page_id]
            return {
                "page_id": page_id,
                "source_order": source[page_id],
                "page_order": order,
                "content_url": f"/api/assessment-core/scan-pages/{page_id}/content",
                "ai_proposal": {
                    "disposition": p.disposition,
                    "confidence": p.confidence,
                    "evidence_code": p.evidence_code,
                    "evidence_summary": p.evidence_summary,
                },
            }

        groups = []
        entries = (
            await self.s.execute(
                select(ScanGroupingEntry, AssignmentParticipant, Student)
                .select_from(ScanGroupingEntry)
                .join(
                    AssignmentParticipant,
                    AssignmentParticipant.id
                    == ScanGroupingEntry.assignment_participant_id,
                )
                .join(Student, Student.id == AssignmentParticipant.student_id)
                .where(ScanGroupingEntry.grouping_revision_id == rev.id)
                .order_by(ScanGroupingEntry.position)
            )
        ).all()
        for e, p, s in entries:
            pages = (
                await self.s.scalars(
                    select(ScanGroupingPage)
                    .where(ScanGroupingPage.grouping_entry_id == e.id)
                    .order_by(ScanGroupingPage.page_order)
                )
            ).all()
            groups.append(
                {
                    "assignment_participant_id": p.id,
                    "student_id": p.student_id,
                    "display_name": s.display_name,
                    "assigned_variant_id": p.assigned_variant_id,
                    "pages": [page_view(x.scan_page_id, x.page_order) for x in pages],
                }
            )
        unmatched = (
            await self.s.scalars(
                select(ScanGroupingUnmatchedPage).where(
                    ScanGroupingUnmatchedPage.grouping_revision_id == rev.id
                )
            )
        ).all()
        papers = (
            await self.s.scalars(
                select(PaperSubmission)
                .where(PaperSubmission.grouping_revision_id == rev.id)
                .order_by(PaperSubmission.created_at, PaperSubmission.id)
            )
        ).all()
        return {
            "batch_id": batch.id,
            "batch_status": batch.status,
            "matching_revision": batch.matching_revision,
            "grouping_revision": rev.revision,
            "grouping_status": rev.status,
            "row_version": rev.row_version,
            "groups": groups,
            "unresolved_pages": [
                page_view(x.scan_page_id)
                | {
                    "candidate_students": candidates.get(x.scan_page_id, []),
                    "reason_code": x.reason_code,
                }
                for x in sorted(unmatched, key=lambda x: source[x.scan_page_id])
            ],
            "paper_submissions": [
                {
                    "id": p.id,
                    "batch_id": p.batch_id,
                    "grouping_revision_id": p.grouping_revision_id,
                    "assignment_id": p.assignment_id,
                    "assignment_participant_id": p.assignment_participant_id,
                    "student_id": p.student_id,
                    "assigned_variant_id": p.assigned_variant_id,
                    "status": p.status,
                    "created_at": p.created_at,
                    "created_by_user_id": p.created_by_user_id,
                }
                for p in papers
            ],
        }

    async def _editable(self, batch_id, expected_revision, expected_version):
        batch = await self._lock_batch(batch_id)
        rev = await self._current_revision(batch_id, lock=True)
        if batch is None or rev is None:
            raise GroupingRepositoryError("grouping_not_found")
        if rev.revision != expected_revision or rev.row_version != expected_version:
            raise GroupingRepositoryError("grouping_revision_conflict")
        if rev.status != "draft" or batch.status != "matching_review_required":
            raise GroupingRepositoryError("grouping_immutable")
        if rev.based_on_matching_revision != batch.matching_revision:
            raise GroupingRepositoryError("grouping_based_on_stale_matching")
        rev.row_version += 1
        rev.updated_at = datetime.now(timezone.utc)
        return batch, rev

    async def _validate(self, batch, rev):
        page_rows = (
            (
                await self.s.execute(
                    select(ScanPage)
                    .join(ScanBatchArtifact)
                    .where(ScanBatchArtifact.batch_id == batch.id)
                )
            )
            .scalars()
            .all()
        )
        entries = (
            await self.s.scalars(
                select(ScanGroupingEntry).where(
                    ScanGroupingEntry.grouping_revision_id == rev.id
                )
            )
        ).all()
        pages = (
            await self.s.scalars(
                select(ScanGroupingPage).where(
                    ScanGroupingPage.grouping_revision_id == rev.id
                )
            )
        ).all()
        unmatched = (
            await self.s.scalars(
                select(ScanGroupingUnmatchedPage).where(
                    ScanGroupingUnmatchedPage.grouping_revision_id == rev.id
                )
            )
        ).all()
        by_entry = {e.id: [] for e in entries}
        for p in pages:
            by_entry[p.grouping_entry_id].append(p)
        contract = PageGroupingRevision(
            expected_revision=rev.revision,
            groups=tuple(
                ConfirmedPaperGroup(
                    assignment_participant_id=e.assignment_participant_id,
                    ordered_scan_page_ids=tuple(
                        p.scan_page_id
                        for p in sorted(by_entry[e.id], key=lambda p: p.page_order)
                    ),
                )
                for e in entries
            ),
            unmatched_scan_page_ids=tuple(x.scan_page_id for x in unmatched),
        )
        participant_ids = set(
            (
                await self.s.scalars(
                    select(AssignmentParticipant.id).where(
                        AssignmentParticipant.assignment_id == batch.assignment_id
                    )
                )
            ).all()
        )
        try:
            validate_grouping_revision(
                contract,
                known_page_ids={p.id for p in page_rows},
                assignment_participant_ids=participant_ids,
                confirm=True,
            )
        except ValueError as exc:
            raise GroupingRepositoryError(str(exc)) from None
        if not entries:
            raise GroupingRepositoryError("grouping_requires_group")
        if any(p.status != "ready" for p in page_rows):
            raise GroupingRepositoryError("grouping_pages_not_ready")
        grouped_participants = {entry.assignment_participant_id for entry in entries}
        existing_paper_participants = set(
            (
                await self.s.scalars(
                    select(PaperSubmission.assignment_participant_id).where(
                        PaperSubmission.batch_id == batch.id
                    )
                )
            ).all()
        )
        if grouped_participants & existing_paper_participants:
            raise GroupingRepositoryError("paper_submission_conflict")
        for e in entries:
            ordered = sorted(by_entry[e.id], key=lambda p: p.page_order)
            if not ordered or [p.page_order for p in ordered] != list(
                range(len(ordered))
            ):
                raise GroupingRepositoryError("grouping_page_order_invalid")
            participant = await self.s.get(
                AssignmentParticipant, e.assignment_participant_id
            )
            if participant is None or participant.assignment_id != batch.assignment_id:
                raise GroupingRepositoryError("participant_not_in_assignment")
            if participant.assigned_variant_id is None:
                raise GroupingRepositoryError("grouping_variant_required")

    async def _compact_entry(self, entry_id, revision_id):
        rows = (
            await self.s.scalars(
                select(ScanGroupingPage)
                .where(ScanGroupingPage.grouping_entry_id == entry_id)
                .order_by(ScanGroupingPage.page_order)
            )
        ).all()
        if not rows:
            await self.s.execute(
                delete(ScanGroupingEntry).where(ScanGroupingEntry.id == entry_id)
            )
            await self._compact_positions(revision_id)
            return
        for n, p in enumerate(rows):
            p.page_order = -(n + 1)
        await self.s.flush()
        for n, p in enumerate(rows):
            p.page_order = n

    async def _compact_positions(self, revision_id):
        rows = (
            await self.s.scalars(
                select(ScanGroupingEntry)
                .where(ScanGroupingEntry.grouping_revision_id == revision_id)
                .order_by(ScanGroupingEntry.position)
            )
        ).all()
        for n, e in enumerate(rows):
            e.position = -(n + 1)
        await self.s.flush()
        for n, e in enumerate(rows):
            e.position = n

    async def _lock_batch(self, batch_id):
        return await self.s.scalar(
            select(AssessmentScanBatch)
            .where(AssessmentScanBatch.id == batch_id)
            .with_for_update()
        )

    async def _current_revision(self, batch_id, lock=False):
        q = (
            select(ScanGroupingRevision)
            .where(ScanGroupingRevision.batch_id == batch_id)
            .order_by(ScanGroupingRevision.revision.desc())
            .limit(1)
        )
        if lock:
            q = q.with_for_update()
        return await self.s.scalar(q)

    def _event(self, batch, aggregate_type, aggregate_id, event, actor, details):
        self.s.add(
            ScanCheckingEvent(
                batch_id=batch,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event,
                actor_user_id=actor,
                details=details,
            )
        )
