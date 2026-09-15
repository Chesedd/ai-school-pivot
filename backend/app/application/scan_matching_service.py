"""Orchestration for immutable AI scan matching with no transaction over AI calls."""

from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
import json
from sqlalchemy import select
from app.application.scan_checking_contracts import (
    MATCHING_OUTPUT_VERSION,
    MatchingAssessmentContext,
    MatchingPage,
    MatchingRosterEntry,
    PageMatchingRequest,
    ProviderContentDescriptor,
    validate_matching_response,
)
from app.application.scan_matching import (
    MATCHING_PROMPT_NAME,
    MATCHING_PROMPT_TEMPLATE_HASH,
    MATCHING_PROMPT_VERSION,
    chunk_page_tokens,
    opaque_token,
    prepare_matching_preview,
)
from app.infrastructure.assessment_models import (
    Assignment,
    AssignmentParticipant,
    Assessment,
    Student,
)
from app.infrastructure.authoring_models import InputArtifact
from app.infrastructure.scan_checking_models import (
    ScanBatchArtifact,
    ScanMatchingChunk,
    ScanMatchingPageEntry,
    ScanMatchingRosterEntry,
    ScanMatchingRun,
    ScanPage,
    ScanPageMatchCandidate,
    ScanPageMatchProposal,
)
from app.infrastructure.scan_matching_repository import SqlAlchemyScanMatchingRepository


class ScanMatchingError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ScanMatchingService:
    def __init__(self, session, storage, provider):
        self.s = session
        self.storage = storage
        self.provider = provider
        self.repo = SqlAlchemyScanMatchingRepository(session)

    async def run(self, batch_id, actor_id):
        run = await self._begin_or_resume(batch_id, actor_id)
        chunks = (
            await self.s.scalars(
                select(ScanMatchingChunk)
                .where(ScanMatchingChunk.matching_run_id == run.id)
                .order_by(ScanMatchingChunk.chunk_index)
            )
        ).all()
        for initial in chunks:
            claimed = await self.repo.claim_chunk(run.id, initial.chunk_index)
            if claimed is None:
                continue
            try:
                await self._execute(run, claimed)
            except Exception as exc:
                await self.s.rollback()
                chunk = await self.s.get(ScanMatchingChunk, claimed.id)
                retryable = getattr(
                    getattr(exc, "code", None), "value", getattr(exc, "code", None)
                ) in {
                    "timeout",
                    "rate_limit",
                    "connection_error",
                    "provider_unavailable",
                }
                chunk.status = "failed_retryable" if retryable else "failed_terminal"
                chunk.failure_code = (
                    getattr(getattr(exc, "code", None), "value", None)
                    or "unexpected_internal_error"
                )
                chunk.completed_at = datetime.now(timezone.utc)
                runrow = await self.s.get(ScanMatchingRun, run.id)
                runrow.status = chunk.status
                runrow.failure_code = chunk.failure_code
                runrow.completed_at = chunk.completed_at
                await self.s.commit()
                raise ScanMatchingError(chunk.failure_code) from None
        await self._finalize(run.id, batch_id)
        return await self.read(batch_id)

    async def _begin_or_resume(self, batch_id, actor_id):
        batch = await self.repo.lock_batch(batch_id)
        if batch is None:
            raise ScanMatchingError("scan_batch_not_found")
        current = await self.repo.current_run(batch_id)
        if batch.status == "matching":
            if current and current.status == "failed_retryable":
                current.status = "running"
                current.failure_code = None
                current.completed_at = None
                await self.s.commit()
                return current
            raise ScanMatchingError("matching_already_running")
        if batch.status == "matching_review_required":
            raise ScanMatchingError("matching_already_completed")
        if batch.status != "extracting":
            raise ScanMatchingError("scan_batch_not_ready_for_matching")
        artifacts = (
            await self.s.scalars(
                select(ScanBatchArtifact)
                .where(ScanBatchArtifact.batch_id == batch_id)
                .order_by(ScanBatchArtifact.upload_position)
            )
        ).all()
        if not artifacts or any(a.extraction_status != "completed" for a in artifacts):
            raise ScanMatchingError("scan_pages_incomplete")
        page_rows = (
            await self.s.execute(
                select(ScanPage, InputArtifact, ScanBatchArtifact.upload_position)
                .join(
                    ScanBatchArtifact,
                    ScanBatchArtifact.id == ScanPage.batch_artifact_id,
                )
                .outerjoin(
                    InputArtifact,
                    InputArtifact.id == ScanPage.derived_render_artifact_id,
                )
                .where(ScanBatchArtifact.batch_id == batch_id)
                .order_by(ScanBatchArtifact.upload_position, ScanPage.source_page_index)
            )
        ).all()
        if not page_rows or any(
            p.status in {"failed_retryable", "failed_terminal"} for p, _, _ in page_rows
        ):
            raise ScanMatchingError("scan_pages_failed")
        if any(p.status != "ready" for p, _, _ in page_rows):
            raise ScanMatchingError("scan_pages_incomplete")
        if any(
            i is None
            or i.mime_type != "image/png"
            or p.coordinate_space_version != "normalized_upright_v1"
            or p.content_fingerprint != i.content_hash_sha256
            for p, i, _ in page_rows
        ):
            raise ScanMatchingError("missing_derived_render")
        assignment, assessment = (
            await self.s.execute(
                select(Assignment, Assessment)
                .join(Assessment, Assessment.id == Assignment.assessment_id)
                .where(Assignment.id == batch.assignment_id)
            )
        ).one()
        roster = (
            await self.s.execute(
                select(AssignmentParticipant, Student)
                .join(Student, Student.id == AssignmentParticipant.student_id)
                .where(AssignmentParticipant.assignment_id == assignment.id)
                .order_by(Student.display_name, AssignmentParticipant.id)
            )
        ).all()
        if not roster:
            raise ScanMatchingError("assignment_has_no_participants")
        batch.matching_revision += 1
        batch.status = "matching"
        batch.row_version += 1
        context = {
            "assignment_id": str(assignment.id),
            "title": assessment.title,
            "roster": [str(p.id) for p, _ in roster],
            "pages": [str(p.id) for p, _, _ in page_rows],
        }
        fingerprint = sha256(
            json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        run = ScanMatchingRun(
            batch_id=batch.id,
            revision=batch.matching_revision,
            requested_by_user_id=actor_id,
            status="running",
            prompt_name=MATCHING_PROMPT_NAME,
            prompt_version=MATCHING_PROMPT_VERSION,
            prompt_template_hash=MATCHING_PROMPT_TEMPLATE_HASH,
            output_schema_version=MATCHING_OUTPUT_VERSION,
            provider_route=f"{self.provider.provider_id}:{self.provider.model_id}",
            request_context_fingerprint=fingerprint,
            assessment_title_snapshot=assessment.title,
        )
        self.s.add(run)
        await self.s.flush()
        for n, (p, student) in enumerate(roster):
            self.s.add(
                ScanMatchingRosterEntry(
                    matching_run_id=run.id,
                    roster_token=opaque_token("roster", n),
                    assignment_participant_id=p.id,
                    display_name_snapshot=student.display_name,
                    position=n,
                )
            )
        tokens = []
        for n, (p, _, _) in enumerate(page_rows):
            token = opaque_token("page", n)
            tokens.append(token)
            self.s.add(
                ScanMatchingPageEntry(
                    matching_run_id=run.id,
                    page_token=token,
                    scan_page_id=p.id,
                    source_order=n,
                )
            )
        for c in chunk_page_tokens(tokens):
            self.s.add(
                ScanMatchingChunk(
                    matching_run_id=run.id,
                    chunk_index=c.index,
                    status="pending",
                    primary_page_tokens=list(c.primary_tokens),
                    context_page_tokens=list(c.context_tokens),
                    provider_id=self.provider.provider_id,
                    model_id=self.provider.model_id,
                )
            )
        await self.s.commit()
        return run

    async def _execute(self, run, chunk):
        roster = (
            await self.s.scalars(
                select(ScanMatchingRosterEntry)
                .where(ScanMatchingRosterEntry.matching_run_id == run.id)
                .order_by(ScanMatchingRosterEntry.position)
            )
        ).all()
        pages = (
            await self.s.execute(
                select(ScanMatchingPageEntry, ScanPage, InputArtifact)
                .join(ScanPage, ScanPage.id == ScanMatchingPageEntry.scan_page_id)
                .join(
                    InputArtifact,
                    InputArtifact.id == ScanPage.derived_render_artifact_id,
                )
                .where(
                    ScanMatchingPageEntry.matching_run_id == run.id,
                    ScanMatchingPageEntry.page_token.in_(chunk.primary_page_tokens),
                )
                .order_by(ScanMatchingPageEntry.source_order)
            )
        ).all()
        descriptors = []
        content = {}
        for entry, page, artifact in pages:
            raw = await self.storage.read(artifact.storage_reference)
            preview = prepare_matching_preview(raw)
            if sha256(raw).hexdigest() != artifact.content_hash_sha256:
                raise ScanMatchingError("page_content_integrity_failure")
            ct = f"content-{entry.page_token}"
            content[ct] = preview.content
            descriptors.append(
                MatchingPage(
                    page_token=entry.page_token,
                    source_order=entry.source_order,
                    content=ProviderContentDescriptor(
                        content_token=ct,
                        mime_type="image/png",
                        content_sha256=preview.sha256,
                    ),
                )
            )
        request = PageMatchingRequest(
            batch_token=f"batch-revision-{run.revision}",
            assessment=MatchingAssessmentContext(title=run.assessment_title_snapshot),
            roster=tuple(
                MatchingRosterEntry(
                    roster_token=r.roster_token, display_name=r.display_name_snapshot
                )
                for r in roster
            ),
            pages=tuple(descriptors),
        )
        response, telemetry = await self.provider.match(request, content)
        validate_matching_response(request, response)
        token_participant = {
            r.roster_token: r.assignment_participant_id for r in roster
        }
        token_page = {e.page_token: e.scan_page_id for e, _, _ in pages}
        for result in response.pages:
            pid = token_participant.get(result.proposed_roster_token)
            page_id = token_page[result.page_token]
            self.s.add(
                ScanPageMatchProposal(
                    matching_run_id=run.id,
                    scan_page_id=page_id,
                    disposition=result.disposition,
                    proposed_assignment_participant_id=pid,
                    confidence=result.confidence,
                    evidence_code=result.evidence_code,
                    evidence_summary=result.evidence_summary,
                    proposed_group_token=result.proposed_group_token,
                    proposed_page_order=result.proposed_page_order,
                    provider_requires_human_review=result.requires_human_review,
                )
            )
            for rank, token in enumerate(result.candidate_roster_tokens, 1):
                self.s.add(
                    ScanPageMatchCandidate(
                        matching_run_id=run.id,
                        scan_page_id=page_id,
                        assignment_participant_id=token_participant[token],
                        rank=rank,
                    )
                )
        chunk.status = "succeeded"
        chunk.validated_output = response.model_dump(mode="json")
        chunk.provider_request_id = telemetry.provider_request_id
        chunk.input_tokens = telemetry.input_tokens
        chunk.output_tokens = telemetry.output_tokens
        chunk.cached_tokens = telemetry.cached_tokens
        chunk.cache_write_tokens = telemetry.cache_write_tokens
        chunk.latency_ms = telemetry.latency_ms
        chunk.completed_at = datetime.now(timezone.utc)
        await self.s.commit()

    async def _finalize(self, run_id, batch_id):
        run = await self.s.get(ScanMatchingRun, run_id)
        total = len(
            (
                await self.s.scalars(
                    select(ScanMatchingPageEntry).where(
                        ScanMatchingPageEntry.matching_run_id == run_id
                    )
                )
            ).all()
        )
        if await self.repo.proposal_count(run_id) != total:
            raise ScanMatchingError("matching_page_coverage_mismatch")
        batch = await self.repo.lock_batch(batch_id)
        run.status = "succeeded"
        run.completed_at = datetime.now(timezone.utc)
        run.failure_code = None
        batch.status = "matching_review_required"
        batch.row_version += 1
        await self.s.commit()

    async def read(self, batch_id):
        run = await self.repo.current_run(batch_id)
        if run is None:
            raise ScanMatchingError("matching_not_found")
        rows = (
            await self.s.execute(
                select(
                    ScanPageMatchProposal,
                    ScanMatchingPageEntry,
                    ScanMatchingRosterEntry,
                )
                .join(
                    ScanMatchingPageEntry,
                    (
                        ScanMatchingPageEntry.matching_run_id
                        == ScanPageMatchProposal.matching_run_id
                    )
                    & (
                        ScanMatchingPageEntry.scan_page_id
                        == ScanPageMatchProposal.scan_page_id
                    ),
                )
                .outerjoin(
                    ScanMatchingRosterEntry,
                    (
                        ScanMatchingRosterEntry.matching_run_id
                        == ScanPageMatchProposal.matching_run_id
                    )
                    & (
                        ScanMatchingRosterEntry.assignment_participant_id
                        == ScanPageMatchProposal.proposed_assignment_participant_id
                    ),
                )
                .where(ScanPageMatchProposal.matching_run_id == run.id)
                .order_by(ScanMatchingPageEntry.source_order)
            )
        ).all()
        candidate_rows = (
            await self.s.execute(
                select(ScanPageMatchCandidate, ScanMatchingRosterEntry)
                .join(
                    ScanMatchingRosterEntry,
                    (
                        ScanMatchingRosterEntry.matching_run_id
                        == ScanPageMatchCandidate.matching_run_id
                    )
                    & (
                        ScanMatchingRosterEntry.assignment_participant_id
                        == ScanPageMatchCandidate.assignment_participant_id
                    ),
                )
                .where(ScanPageMatchCandidate.matching_run_id == run.id)
                .order_by(
                    ScanPageMatchCandidate.scan_page_id, ScanPageMatchCandidate.rank
                )
            )
        ).all()
        candidates = {}
        for candidate, roster_entry in candidate_rows:
            candidates.setdefault(candidate.scan_page_id, []).append(
                {
                    "assignment_participant_id": candidate.assignment_participant_id,
                    "display_name": roster_entry.display_name_snapshot,
                }
            )
        pages = [
            {
                "page_id": p.scan_page_id,
                "source_order": e.source_order,
                "content_url": f"/api/assessment-core/scan-pages/{p.scan_page_id}/content",
                "disposition": p.disposition,
                "proposed_student": None
                if r is None
                else {
                    "assignment_participant_id": r.assignment_participant_id,
                    "display_name": r.display_name_snapshot,
                },
                "candidate_students": candidates.get(p.scan_page_id, []),
                "confidence": p.confidence,
                "evidence_code": p.evidence_code,
                "evidence_summary": p.evidence_summary,
                "proposed_page_order": p.proposed_page_order,
                "warning": p.disposition != "matched"
                or p.provider_requires_human_review,
            }
            for p, e, r in rows
        ]
        counts = {
            x: sum(p["disposition"] == x for p in pages)
            for x in ("matched", "ambiguous", "unmatched")
        }
        return {
            "batch_id": batch_id,
            "matching_revision": run.revision,
            "status": "matching_review_required"
            if run.status == "succeeded"
            else run.status,
            "pages_summary": {"total": len(pages), **counts},
            "warnings": sum(p["warning"] for p in pages),
            "proposals": pages,
        }
