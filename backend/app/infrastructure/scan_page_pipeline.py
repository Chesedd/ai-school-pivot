"""Transactional upload and resumable extraction orchestration for scan intake."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.input_artifacts import (
    MAX_ARTIFACT_SIZE_BYTES,
    ArtifactError,
    detected_mime_type,
)
from app.application.paper_operational_limits import PaperOperationalLimits
from app.application.scan_checking_contracts import NORMALIZED_UPRIGHT_V1
from app.application.scan_intake import ScanIntakeError, _filename
from app.application.scan_page_extraction import ExtractionCounts, ExtractionSummary
from app.infrastructure.authoring_models import InputArtifact
from app.infrastructure.paper_operational_guards import guard_attachment, page_count
from app.infrastructure.scan_checking_models import (
    AssessmentScanBatch,
    ScanBatchArtifact,
    ScanCheckingEvent,
    ScanPage,
)
from app.infrastructure.scan_page_rasterizer import (
    PillowPdfiumPageRasterizer,
    RasterizationError,
)


class ScanPagePipeline:
    def __init__(self, session: AsyncSession, storage, rasterizer=None, limits=None):
        self.session, self.storage = session, storage
        self.limits = limits or PaperOperationalLimits()
        self.rasterizer = rasterizer or PillowPdfiumPageRasterizer(self.limits)

    async def upload(
        self,
        *,
        batch_id: UUID,
        actor_id: UUID,
        content: bytes,
        claimed_mime_type: str,
        upload_position: int,
        filename: str,
    ):
        if not isinstance(upload_position, int) or upload_position < 0:
            raise ScanIntakeError("invalid_upload_position")
        filename = _filename(filename)
        if not content:
            raise ArtifactError("artifact_too_small")
        self.limits.guard("max_single_artifact_bytes", len(content), "scan_artifact_too_large", ScanIntakeError)
        if len(content) > MAX_ARTIFACT_SIZE_BYTES:
            raise ArtifactError("artifact_too_large")
        actual = detected_mime_type(content)
        if actual is None or actual != claimed_mime_type:
            raise ArtifactError("invalid_artifact_signature")
        reference = None
        try:
            batch = await self.session.scalar(
                select(AssessmentScanBatch)
                .where(AssessmentScanBatch.id == batch_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if batch is None:
                raise ScanIntakeError("scan_batch_not_found")
            if batch.status not in {"draft", "uploading"}:
                raise ScanIntakeError("artifact_attachment_not_allowed")
            await guard_attachment(self.session, batch.id, len(content), self.limits)
            reference = await self.storage.store(content, actual)
            artifact = InputArtifact(
                owner_id=actor_id,
                mime_type=actual,
                content_hash_sha256=sha256(content).hexdigest(),
                size_bytes=len(content),
                storage_reference=reference,
            )
            self.session.add(artifact)
            await self.session.flush()
            attached = ScanBatchArtifact(
                batch_id=batch.id,
                input_artifact_id=artifact.id,
                upload_position=upload_position,
                original_filename=filename,
            )
            self.session.add(attached)
            await self.session.flush()
            if batch.status == "draft":
                batch.status = "uploading"
                batch.row_version += 1
            self.session.add(
                ScanCheckingEvent(
                    batch_id=batch.id,
                    aggregate_type="artifact",
                    aggregate_id=attached.id,
                    event_type="artifact.attached",
                    actor_user_id=actor_id,
                    details={"upload_position": upload_position},
                )
            )
            await self.session.commit()
            await self.session.refresh(attached)
            return attached, artifact
        except IntegrityError as exc:
            await self.session.rollback()
            if reference is not None:
                await self.storage.delete(reference)
            raise ScanIntakeError("duplicate_upload_position") from exc
        except BaseException:
            await self.session.rollback()
            if reference is not None:
                await self.storage.delete(reference)
            raise

    async def extract(self, *, batch_id: UUID, actor_id: UUID) -> ExtractionSummary:
        batch = await self.session.scalar(
            select(AssessmentScanBatch)
            .where(AssessmentScanBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch is None:
            raise ScanIntakeError("scan_batch_not_found")
        artifacts = (
            await self.session.scalars(
                select(ScanBatchArtifact)
                .where(ScanBatchArtifact.batch_id == batch_id)
                .order_by(ScanBatchArtifact.upload_position)
            )
        ).all()
        if not artifacts:
            raise ScanIntakeError("scan_batch_has_no_artifacts")
        if batch.status == "uploading":
            batch.status = "extracting"
            batch.row_version += 1
            await self.session.commit()
        elif batch.status != "extracting":
            raise ScanIntakeError("extraction_not_allowed")
        artifact_ids = [artifact.id for artifact in artifacts]
        for artifact_id in artifact_ids:
            await self._extract_artifact(artifact_id, actor_id)
        artifacts = (
            await self.session.scalars(
                select(ScanBatchArtifact).where(ScanBatchArtifact.batch_id == batch_id)
            )
        ).all()
        pages = (
            await self.session.scalars(
                select(ScanPage)
                .join(ScanBatchArtifact)
                .where(ScanBatchArtifact.batch_id == batch_id)
            )
        ).all()
        ac = {
            k: sum(a.extraction_status == k for a in artifacts)
            for k in ("completed", "failed_retryable", "failed_terminal")
        }
        pc = {
            k: sum(p.status == k for p in pages)
            for k in ("ready", "failed_retryable", "failed_terminal")
        }
        return ExtractionSummary(batch_id, "extracting", ExtractionCounts(**ac), pc)

    async def _extract_artifact(self, artifact_id: UUID, actor_id: UUID):
        attached = await self.session.get(ScanBatchArtifact, artifact_id)
        batch_id = attached.batch_id
        # Hold the same aggregate lock for preflight, rendering and atomic persistence.
        await self.session.scalar(select(AssessmentScanBatch).where(
            AssessmentScanBatch.id == attached.batch_id).with_for_update().execution_options(populate_existing=True))
        await self.session.refresh(attached)
        if attached.extraction_status in {"completed", "failed_terminal"}:
            return
        original = await self.session.get(InputArtifact, attached.input_artifact_id)
        references = []
        previous = attached.extraction_status
        attached.extraction_status = "running"
        attached.failure_code = None
        self._event(
            attached,
            actor_id,
            "artifact",
            "artifact.extraction_status_changed",
            {"from": previous, "to": "running"},
        )
        await self.session.flush()
        try:
            self.limits.guard("max_single_artifact_bytes", original.size_bytes,
                "scan_artifact_too_large", RasterizationError)
            reader = getattr(self.storage, "read_bounded", None)
            content = (await reader(original.storage_reference, self.limits.max_single_artifact_bytes)
                       if reader else await self.storage.read(original.storage_reference))
            if (
                len(content) != original.size_bytes
                or sha256(content).hexdigest() != original.content_hash_sha256
            ):
                raise RasterizationError("artifact_integrity_failed")
            counter = getattr(self.rasterizer, "page_count", None)
            if counter is not None:
                count = counter(content, original.mime_type)
                self.limits.guard("max_pages_per_artifact", count,
                    "scan_artifact_page_limit_exceeded", RasterizationError)
                own = await self.session.scalar(select(func.count(ScanPage.id)).where(
                    ScanPage.batch_artifact_id == attached.id))
                self.limits.guard("max_pages_per_batch",
                    await page_count(self.session, attached.batch_id) - own + count,
                    "scan_batch_page_limit_exceeded", RasterizationError)
            seen = 0
            for rendered in self.rasterizer.rasterize(content, original.mime_type):
                self.limits.guard("max_pages_per_artifact", rendered.source_page_index + 1,
                    "scan_artifact_page_limit_exceeded", RasterizationError)
                seen = max(seen, rendered.source_page_index + 1)
                existing = await self.session.scalar(
                    select(ScanPage).where(
                        ScanPage.batch_artifact_id == attached.id,
                        ScanPage.source_page_index == rendered.source_page_index,
                    )
                )
                if existing is not None and existing.status in {
                    "ready",
                    "failed_terminal",
                }:
                    continue
                await self._persist_page(
                    attached, existing, rendered, original.owner_id, actor_id, references
                )
            attached = await self.session.get(ScanBatchArtifact, artifact_id)
            attached.extraction_status = "completed"
            attached.page_count = seen
            attached.failure_code = None
            self._event(
                attached,
                actor_id,
                "artifact",
                "artifact.extraction_status_changed",
                {"from": "running", "to": "completed"},
            )
            await self.session.commit()
        except RasterizationError as exc:
            await self.session.rollback()
            for reference in references:
                await self.storage.delete(reference)
            await self.session.scalar(select(AssessmentScanBatch).where(
                AssessmentScanBatch.id == batch_id).with_for_update())
            attached = await self.session.get(ScanBatchArtifact, artifact_id)
            if attached.extraction_status == "completed":
                return
            target = "failed_retryable" if exc.retryable else "failed_terminal"
            attached.extraction_status = target
            attached.failure_code = exc.code
            self._event(
                attached,
                actor_id,
                "artifact",
                "artifact.extraction_status_changed",
                {"from": "running", "to": target},
            )
            await self.session.commit()
        except BaseException as exc:
            await self.session.rollback()
            for reference in references:
                await self.storage.delete(reference)
            if not isinstance(exc, Exception):
                raise
            await self.session.scalar(select(AssessmentScanBatch).where(
                AssessmentScanBatch.id == batch_id).with_for_update())
            attached = await self.session.get(ScanBatchArtifact, artifact_id)
            if attached.extraction_status == "completed":
                return
            attached.extraction_status = "failed_retryable"
            attached.failure_code = "page_render_failed"
            self._event(
                attached,
                actor_id,
                "artifact",
                "artifact.extraction_status_changed",
                {"from": "running", "to": "failed_retryable"},
            )
            await self.session.commit()

    async def _persist_page(self, attached, existing, rendered, owner_id, actor_id, references):
        self.limits.guard("max_pixels_per_page", rendered.width_px * rendered.height_px,
            "image_too_large", RasterizationError)
        if existing is None:
            self.limits.guard("max_pages_per_batch", await page_count(self.session, attached.batch_id) + 1,
                "scan_batch_page_limit_exceeded", RasterizationError)
        reference = await self.storage.store(rendered.png_bytes, "image/png")
        references.append(reference)
        try:
            derived = InputArtifact(
                owner_id=owner_id,
                mime_type="image/png",
                content_hash_sha256=rendered.content_hash_sha256,
                size_bytes=len(rendered.png_bytes),
                storage_reference=reference,
            )
            self.session.add(derived)
            await self.session.flush()
            if existing is None:
                page = ScanPage(
                    batch_artifact_id=attached.id,
                    source_page_index=rendered.source_page_index,
                )
                self.session.add(page)
                await self.session.flush()
                self._event(
                    attached,
                    actor_id,
                    "page",
                    "page.created",
                    {"source_page_index": rendered.source_page_index},
                    page.id,
                )
            else:
                page = existing
            old = page.status
            page.derived_render_artifact_id = derived.id
            page.width_px = rendered.width_px
            page.height_px = rendered.height_px
            page.rotation_degrees = 0
            page.coordinate_space_version = NORMALIZED_UPRIGHT_V1
            page.content_fingerprint = rendered.content_hash_sha256
            page.status = "ready"
            page.failure_code = None
            self._event(
                attached,
                actor_id,
                "page",
                "page.status_changed",
                {"from": old, "to": "ready"},
                page.id,
            )
            await self.session.flush()
        except BaseException:
            await self.session.rollback()
            await self.storage.delete(reference)
            raise

    def _event(self, attached, actor, aggregate, event, details, aggregate_id=None):
        self.session.add(
            ScanCheckingEvent(
                batch_id=attached.batch_id,
                aggregate_type=aggregate,
                aggregate_id=aggregate_id or attached.id,
                event_type=event,
                actor_user_id=actor,
                details=details,
            )
        )
