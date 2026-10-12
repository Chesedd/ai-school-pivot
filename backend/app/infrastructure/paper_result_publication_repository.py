"""Publication transactions serialize on immutable paper identity, in UUID order."""

import hashlib
import json
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select

from app.application.paper_checking_review import PaperReviewError, canonicalize_draft
from app.application.paper_result_publication import (
    PaperPublicationError,
    PublicationEntry,
    PublicationSnapshot,
    build_publication_snapshot,
)
from app.infrastructure.authoring_models import InputArtifact
from app.infrastructure.paper_checking_review_models import (
    PaperCheckReview,
    PaperCheckReviewRevision,
)
from app.infrastructure.paper_checking_review_repository import (
    PaperCheckingReviewRepository,
)
from app.infrastructure.paper_result_publication_models import (
    PaperResultPublication,
    PaperResultPublicationState,
)
from app.infrastructure.scan_checking_models import (
    PaperSubmission,
    PaperSubmissionPage,
    ScanBatchArtifact,
    ScanPage,
)
from app.infrastructure.scan_storage_retention_models import ScanStorageRetentionEntry


class PaperResultPublicationRepository:
    def __init__(self, db):
        self.db = db

    async def _lock_papers(self, paper_ids, batch_id=None):
        papers = (
            await self.db.scalars(
                select(PaperSubmission)
                .where(PaperSubmission.id.in_(paper_ids))
                .order_by(PaperSubmission.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        if len(papers) != len(paper_ids) or any(
            batch_id is not None and p.batch_id != batch_id for p in papers
        ):
            raise PaperPublicationError("paper_publication_selection_mismatch", 404)
        # Same page locks as retention, after paper locks in deterministic order.
        # Once a committed claim exists, publishing cannot promise those bytes.
        pages = (await self.db.scalars(
            select(ScanPage.id)
            .join(PaperSubmissionPage, PaperSubmissionPage.scan_page_id == ScanPage.id)
            .where(PaperSubmissionPage.paper_submission_id.in_(paper_ids))
            .order_by(ScanPage.id)
            .with_for_update(of=ScanPage)
        )).all()
        if await self.db.scalar(
            select(ScanStorageRetentionEntry.id)
            .where(ScanStorageRetentionEntry.scan_page_id.in_(pages))
            .limit(1)
        ):
            raise PaperPublicationError("scan_page_content_purged", 410)
        return {p.id: p for p in papers}

    async def _snapshot(self, paper, revision_id):
        row = await self.db.get(PaperCheckReviewRevision, revision_id)
        if row is None:
            raise PaperPublicationError("paper_publication_revision_not_found", 404)
        if row.paper_submission_id != paper.id:
            raise PaperPublicationError("paper_publication_revision_mismatch")
        if row.revision_kind != "approved":
            raise PaperPublicationError("paper_publication_revision_not_approved")
        review = await self.db.get(PaperCheckReview, row.review_id)
        if review is None:
            raise PaperPublicationError("paper_publication_review_not_found", 404)
        if (review.paper_submission_id, review.check_run_id, review.ai_revision_id) != (
            paper.id,
            row.check_run_id,
            row.ai_revision_id,
        ):
            raise PaperPublicationError("paper_publication_revision_mismatch")
        repo = PaperCheckingReviewRepository(self.db)
        try:
            _, run, ai = await repo._source(paper.id, row.check_run_id)
            policy, order = await repo._policy(paper, run, ai)
            if (row.grading_policy_revision, row.grading_policy_fingerprint) != (
                ai.revision
                if row.source_kind == "manual"
                else ai.grading_policy_revision,
                ai.policy_fingerprint
                if row.source_kind == "manual"
                else ai.grading_policy_fingerprint,
            ):
                raise ValueError("policy_provenance")
            revision = repo._revision_response(row)
            pages = set(
                (
                    await self.db.scalars(
                        select(PaperSubmissionPage.scan_page_id).where(
                            PaperSubmissionPage.paper_submission_id == paper.id
                        )
                    )
                ).all()
            )
            if pages != {p.id for p in (await self._page_rows(paper.id))}:
                raise ValueError("invalid_membership")
            if run is not None:
                frozen = {x["scan_page_id"] for x in run.input_snapshot["pages"]}
                if {str(x) for x in pages} != frozen:
                    raise ValueError("page_provenance")
            revision = revision.model_copy(
                update={"snapshot": canonicalize_draft(revision.snapshot, order)}
            )
            return build_publication_snapshot(revision, policy, pages)
        except (PaperReviewError, ValidationError, ValueError, KeyError, TypeError):
            raise PaperPublicationError("paper_publication_invalid_snapshot") from None

    async def publish_selected(self, entries, actor, batch_id=None):
        identifiers = [x.paper_submission_id for x in entries]
        if (
            not identifiers
            or len(set(identifiers)) != len(identifiers)
            or len(entries) > 500
        ):
            raise PaperPublicationError("paper_publication_invalid_selection")
        papers = await self._lock_papers(identifiers, batch_id)
        # Validate the complete selection before making any publication writes.
        snapshots = {
            x.paper_submission_id: await self._snapshot(
                papers[x.paper_submission_id], x.approved_review_revision_id
            )
            for x in entries
        }
        results = []
        for entry in sorted(entries, key=lambda x: str(x.paper_submission_id)):
            paper_id = entry.paper_submission_id
            state = await self.db.scalar(
                select(PaperResultPublicationState)
                .where(PaperResultPublicationState.paper_submission_id == paper_id)
                .execution_options(populate_existing=True)
            )
            previous = (
                await self.db.get(PaperResultPublication, state.active_publication_id)
                if state
                else None
            )
            if (
                previous
                and previous.approved_review_revision_id
                == entry.approved_review_revision_id
            ):
                results.append(self.project(previous, True, "already_current"))
                continue
            number = state.version + 1 if state else 1
            publication = PaperResultPublication(
                id=uuid4(),
                paper_submission_id=paper_id,
                review_id=(
                    await self.db.get(
                        PaperCheckReviewRevision, entry.approved_review_revision_id
                    )
                ).review_id,
                approved_review_revision_id=entry.approved_review_revision_id,
                published_by_user_id=actor,
                publication_number=number,
                supersedes_publication_id=previous.id if previous else None,
                publication_snapshot=snapshots[paper_id].model_dump(mode="json"),
            )
            self.db.add(publication)
            await self.db.flush()
            now = await self.db.scalar(select(func.clock_timestamp()))
            if state:
                state.active_publication_id, state.version, state.updated_at = (
                    publication.id,
                    number,
                    now,
                )
            else:
                self.db.add(
                    PaperResultPublicationState(
                        paper_submission_id=paper_id,
                        active_publication_id=publication.id,
                        version=1,
                        updated_at=now,
                    )
                )
            await self.db.flush()
            results.append(self.project(publication, True, "published"))
        return results

    async def publish(self, paper_id, revision_id, actor):
        return (
            await self.publish_selected(
                [
                    PublicationEntry(
                        paper_submission_id=paper_id,
                        approved_review_revision_id=revision_id,
                    )
                ],
                actor,
            )
        )[0]

    async def bulk(self, batch_id, actor):
        papers = (
            await self.db.scalars(
                select(PaperSubmission.id)
                .where(PaperSubmission.batch_id == batch_id)
                .order_by(PaperSubmission.id)
            )
        ).all()
        await self._lock_papers(papers, batch_id)
        entries, ineligible = [], []
        for paper_id in papers:
            revision = await self.db.scalar(
                select(PaperCheckReviewRevision)
                .where(
                    PaperCheckReviewRevision.paper_submission_id == paper_id,
                    PaperCheckReviewRevision.revision_kind == "approved",
                )
                .order_by(
                    PaperCheckReviewRevision.created_at.desc(),
                    PaperCheckReviewRevision.revision_number.desc(),
                    PaperCheckReviewRevision.id.desc(),
                )
                .limit(1)
            )
            if revision is None:
                ineligible.append(str(paper_id))
            else:
                entries.append(
                    PublicationEntry(
                        paper_submission_id=paper_id,
                        approved_review_revision_id=revision.id,
                    )
                )
        if ineligible:
            raise PaperPublicationError(
                "paper_publication_batch_has_ineligible_papers", 409
            )
        return await self.publish_selected(entries, actor, batch_id) if entries else []

    @staticmethod
    def project(row, active, outcome=None):
        # Validate the stored allowlist on read as well; never serialize ORM rows.
        snapshot = PublicationSnapshot.model_validate_json(
            json.dumps(row.publication_snapshot)
        )
        result = {
            "id": row.id,
            "paper_submission_id": row.paper_submission_id,
            "publication_number": row.publication_number,
            "approved_review_revision_id": row.approved_review_revision_id,
            "published_at": row.created_at,
            "active": active,
            "result": snapshot.model_dump(mode="json"),
        }
        if outcome:
            result["outcome"] = outcome
        return result

    async def history(self, paper_id):
        state = await self.db.get(PaperResultPublicationState, paper_id)
        rows = (
            await self.db.scalars(
                select(PaperResultPublication)
                .where(PaperResultPublication.paper_submission_id == paper_id)
                .order_by(PaperResultPublication.publication_number)
            )
        ).all()
        return [
            self.project(x, bool(state and state.active_publication_id == x.id))
            for x in rows
        ]

    async def _owned_publication(self, paper_id, student_id):
        row = (
            await self.db.scalar(
                select(PaperResultPublication)
                .join(
                    PaperResultPublicationState,
                    PaperResultPublicationState.active_publication_id
                    == PaperResultPublication.id,
                )
                .join(
                    PaperSubmission,
                    PaperSubmission.id
                    == PaperResultPublicationState.paper_submission_id,
                )
                .where(
                    PaperSubmission.id == paper_id,
                    PaperSubmission.student_id == student_id,
                )
            )
            if student_id is not None
            else None
        )
        if row is None:
            raise PaperPublicationError("paper_result_not_found", 404)
        return row

    async def _page_rows(self, paper_id):
        return (
            await self.db.scalars(
                select(ScanPage)
                .join(PaperSubmissionPage)
                .where(
                    PaperSubmissionPage.paper_submission_id == paper_id,
                    ScanPage.status == "ready",
                    ScanPage.coordinate_space_version == "normalized_upright_v1",
                    ScanPage.derived_render_artifact_id.is_not(None),
                )
                .order_by(PaperSubmissionPage.page_order)
            )
        ).all()

    async def student_result(self, paper_id, student_id):
        row = await self._owned_publication(paper_id, student_id)
        result = self.project(row, True)
        pages = (
            await self.db.execute(
                select(ScanPage, PaperSubmissionPage.page_order)
                .join(PaperSubmissionPage)
                .where(
                    PaperSubmissionPage.paper_submission_id == paper_id,
                    ScanPage.status == "ready",
                    ScanPage.coordinate_space_version == "normalized_upright_v1",
                )
                .order_by(PaperSubmissionPage.page_order)
            )
        ).all()
        result["pages"] = [
            {
                "scan_page_id": p.id,
                "page_order": order,
                "width": p.width_px,
                "height": p.height_px,
                "coordinate_space_version": p.coordinate_space_version,
                "content_url": f"/api/assessment-core/student/paper-submissions/{paper_id}/pages/{p.id}",
            }
            for p, order in pages
        ]
        return result

    async def student_list(self, student_id, offset=0, limit=20):
        papers = (
            await self.db.scalars(
                select(PaperSubmission.id)
                .join(PaperResultPublicationState)
                .where(PaperSubmission.student_id == student_id)
                .order_by(PaperSubmission.created_at.desc(), PaperSubmission.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [await self.student_result(p, student_id) for p in papers]

    async def student_page(self, paper_id, page_id, student_id, storage):
        await self._owned_publication(paper_id, student_id)
        page = next(
            (x for x in await self._page_rows(paper_id) if x.id == page_id), None
        )
        if page is None:
            raise PaperPublicationError("paper_result_not_found", 404)
        artifact = await self.db.get(InputArtifact, page.derived_render_artifact_id)
        is_source = await self.db.scalar(
            select(ScanBatchArtifact.id)
            .where(
                ScanBatchArtifact.input_artifact_id == page.derived_render_artifact_id
            )
            .limit(1)
        )
        if (
            artifact is None
            or is_source
            or artifact.mime_type != "image/png"
            or artifact.content_hash_sha256 != page.content_fingerprint
        ):
            raise PaperPublicationError("paper_result_not_found", 404)
        try:
            content = await storage.read(artifact.storage_reference)
        except (OSError, ValueError):
            raise PaperPublicationError("paper_result_not_found", 404) from None
        if (
            not content.startswith(b"\x89PNG\r\n\x1a\n")
            or len(content) != artifact.size_bytes
            or hashlib.sha256(content).hexdigest() != page.content_fingerprint
        ):
            raise PaperPublicationError("paper_result_not_found", 404)
        return content
