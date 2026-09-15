"""SQLAlchemy transaction adapter for scan intake (no provider/HTTP concerns)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.scan_checking_contracts import AssessmentScanBatchState
from app.application.scan_intake import (
    ArtifactExtractionStatus,
    AssessmentScanBatchRecord,
    ScanBatchArtifactRecord,
    ScanIntakeError,
    ScanPageRecord,
    ScanPageStatus,
)
from app.infrastructure.model_registry import register_all_models

# Scan intake rows reference tables owned by auth, assessment, classroom, and
# authoring.  Register the complete shared metadata graph when this persistence
# adapter is loaded instead of depending on an unrelated application import
# order to make those foreign-key targets resolvable during flush.
register_all_models()

from app.infrastructure.assessment_models import Assignment  # noqa: E402
from app.infrastructure.authoring_models import InputArtifact  # noqa: E402
from app.infrastructure.scan_checking_models import (  # noqa: E402
    AssessmentScanBatch,
    ScanBatchArtifact,
    ScanCheckingEvent,
    ScanPage,
)


def _constraint_name(exc: IntegrityError) -> str | None:
    original = exc.orig
    driver = getattr(original, "__cause__", None)
    return (
        getattr(original, "constraint_name", None)
        or getattr(driver, "constraint_name", None)
        or getattr(getattr(original, "diag", None), "constraint_name", None)
    )


def _batch(r: AssessmentScanBatch) -> AssessmentScanBatchRecord:
    return AssessmentScanBatchRecord(
        id=r.id,
        assignment_id=r.assignment_id,
        class_group_id=r.class_group_id,
        created_by_user_id=r.created_by_user_id,
        status=AssessmentScanBatchState(r.status),
        instruction_text=r.instruction_text,
        policy_draft=r.policy_draft,
        policy_snapshot=r.policy_snapshot,
        policy_schema_version=r.policy_schema_version,
        policy_fingerprint=r.policy_fingerprint,
        prompt_policy_version=r.prompt_policy_version,
        matching_revision=r.matching_revision,
        row_version=r.row_version,
        request_key=r.request_key,
        request_hash=r.request_hash,
        created_at=r.created_at,
        updated_at=r.updated_at,
        cancelled_at=r.cancelled_at,
        cancelled_by_user_id=r.cancelled_by_user_id,
        cancellation_reason=r.cancellation_reason,
    )


def _artifact(r: ScanBatchArtifact) -> ScanBatchArtifactRecord:
    return ScanBatchArtifactRecord(
        id=r.id,
        batch_id=r.batch_id,
        input_artifact_id=r.input_artifact_id,
        upload_position=r.upload_position,
        original_filename=r.original_filename,
        extraction_status=ArtifactExtractionStatus(r.extraction_status),
        page_count=r.page_count,
        failure_code=r.failure_code,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _page(r: ScanPage) -> ScanPageRecord:
    return ScanPageRecord(
        id=r.id,
        batch_artifact_id=r.batch_artifact_id,
        source_page_index=r.source_page_index,
        derived_render_artifact_id=r.derived_render_artifact_id,
        width_px=r.width_px,
        height_px=r.height_px,
        rotation_degrees=r.rotation_degrees,
        coordinate_space_version=r.coordinate_space_version,
        content_fingerprint=r.content_fingerprint,
        status=ScanPageStatus(r.status),
        failure_code=r.failure_code,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


class SqlAlchemyScanIntakeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def assignment_class(self, assignment_id: UUID) -> UUID | None:
        return await self.db.scalar(
            select(Assignment.class_group_id).where(Assignment.id == assignment_id)
        )

    async def get_batch_by_request(self, actor_id: UUID, request_key: str):
        row = await self.db.scalar(
            select(AssessmentScanBatch).where(
                AssessmentScanBatch.created_by_user_id == actor_id,
                AssessmentScanBatch.request_key == request_key,
            )
        )
        return _batch(row) if row else None

    async def get_batch(self, batch_id: UUID, *, lock: bool = False):
        query = select(AssessmentScanBatch).where(AssessmentScanBatch.id == batch_id)
        row = await self.db.scalar(query.with_for_update() if lock else query)
        return _batch(row) if row else None

    async def list_batches(self, assignment_id: UUID):
        rows = (
            await self.db.scalars(
                select(AssessmentScanBatch)
                .where(AssessmentScanBatch.assignment_id == assignment_id)
                .order_by(
                    AssessmentScanBatch.created_at.desc(), AssessmentScanBatch.id.desc()
                )
            )
        ).all()
        return tuple(_batch(r) for r in rows)

    async def create_batch(self, **values: Any):
        row = AssessmentScanBatch(**values)
        self.db.add(row)
        try:
            await self.db.flush()
            self.db.add(
                ScanCheckingEvent(
                    batch_id=row.id,
                    aggregate_type="batch",
                    aggregate_id=row.id,
                    event_type="batch.created",
                    actor_user_id=row.created_by_user_id,
                    details={"assignment_id": str(row.assignment_id)},
                )
            )
            await self.db.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == "uq_assessment_scan_batches_actor_request":
                raise ScanIntakeError("idempotency_conflict") from exc
            raise
        return _batch(row)

    async def cancel_batch(
        self, batch_id: UUID, actor_id: UUID, reason: str, expected_version: int
    ):
        row = await self.db.scalar(
            select(AssessmentScanBatch)
            .where(AssessmentScanBatch.id == batch_id)
            .with_for_update()
        )
        if row is None:
            raise ScanIntakeError("scan_batch_not_found")
        if row.status == "cancelled":
            return _batch(row)
        if row.row_version != expected_version:
            raise ScanIntakeError("concurrent_scan_batch_update")
        row.status = "cancelled"
        row.cancelled_at = datetime.now(UTC)
        row.cancelled_by_user_id = actor_id
        row.cancellation_reason = reason
        row.row_version += 1
        row.updated_at = datetime.now(UTC)
        self.db.add(
            ScanCheckingEvent(
                batch_id=row.id,
                aggregate_type="batch",
                aggregate_id=row.id,
                event_type="batch.cancelled",
                actor_user_id=actor_id,
                details={"reason_code": "teacher_cancelled"},
            )
        )
        await self.db.flush()
        return _batch(row)

    async def attach_artifact(self, **values: Any):
        owner = await self.db.scalar(
            select(InputArtifact.owner_id).where(
                InputArtifact.id == values["input_artifact_id"]
            )
        )
        if owner is None:
            raise ScanIntakeError("artifact_not_found")
        if owner != values["actor_user_id"]:
            raise ScanIntakeError("artifact_not_owned")
        actor = values.pop("actor_user_id")
        row = ScanBatchArtifact(**values, extraction_status="pending")
        self.db.add(row)
        try:
            await self.db.flush()
            self.db.add(
                ScanCheckingEvent(
                    batch_id=row.batch_id,
                    aggregate_type="artifact",
                    aggregate_id=row.id,
                    event_type="artifact.attached",
                    actor_user_id=actor,
                    details={"upload_position": row.upload_position},
                )
            )
            await self.db.flush()
        except IntegrityError as exc:
            names = {
                "uq_scan_batch_artifacts_input": "duplicate_artifact",
                "uq_scan_batch_artifacts_position": "duplicate_upload_position",
            }
            if _constraint_name(exc) in names:
                raise ScanIntakeError(names[_constraint_name(exc)]) from exc
            raise
        return _artifact(row)

    async def create_page(self, **values: Any):
        actor = values.pop("actor_user_id")
        values["status"] = values["status"].value
        artifact = await self.db.get(ScanBatchArtifact, values["batch_artifact_id"])
        if artifact is None:
            raise ScanIntakeError("batch_artifact_not_found")
        if (
            values.get("derived_render_artifact_id") is not None
            and await self.db.get(InputArtifact, values["derived_render_artifact_id"])
            is None
        ):
            raise ScanIntakeError("derived_artifact_not_found")
        row = ScanPage(**values)
        self.db.add(row)
        try:
            await self.db.flush()
            self.db.add(
                ScanCheckingEvent(
                    batch_id=artifact.batch_id,
                    aggregate_type="page",
                    aggregate_id=row.id,
                    event_type="page.created",
                    actor_user_id=actor,
                    details={"source_page_index": row.source_page_index},
                )
            )
            await self.db.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == "uq_scan_pages_artifact_index":
                raise ScanIntakeError("duplicate_page") from exc
            raise
        return _page(row)

    async def transition_batch(self, **values: Any):
        row = await self.db.scalar(
            select(AssessmentScanBatch)
            .where(AssessmentScanBatch.id == values["batch_id"])
            .with_for_update()
        )
        if row is None:
            raise ScanIntakeError("scan_batch_not_found")
        if row.row_version != values["expected_version"]:
            raise ScanIntakeError("concurrent_scan_batch_update")
        row.status = values["target"].value
        row.row_version += 1
        row.updated_at = datetime.now(UTC)
        await self.db.flush()
        return _batch(row)

    async def transition_artifact(self, **values: Any):
        row = await self.db.scalar(
            select(ScanBatchArtifact)
            .where(ScanBatchArtifact.id == values["artifact_id"])
            .with_for_update()
        )
        if row is None:
            raise ScanIntakeError("scan_artifact_not_found")
        if row.extraction_status != values["current"].value:
            raise ScanIntakeError("concurrent_artifact_update")
        row.extraction_status = values["target"].value
        row.page_count = values["page_count"]
        row.failure_code = values["failure_code"]
        row.updated_at = datetime.now(UTC)
        self.db.add(
            ScanCheckingEvent(
                batch_id=row.batch_id,
                aggregate_type="artifact",
                aggregate_id=row.id,
                event_type="artifact.extraction_status_changed",
                actor_user_id=values["actor_user_id"],
                details={"from": values["current"].value, "to": values["target"].value},
            )
        )
        await self.db.flush()
        return _artifact(row)

    async def transition_page(self, **values: Any):
        row = await self.db.scalar(
            select(ScanPage).where(ScanPage.id == values["page_id"]).with_for_update()
        )
        if row is None:
            raise ScanIntakeError("scan_page_not_found")
        if row.status != values["current"].value:
            raise ScanIntakeError("concurrent_page_update")
        artifact = await self.db.get(ScanBatchArtifact, row.batch_artifact_id)
        old = row.status
        row.status = values["target"].value
        row.failure_code = values.pop("failure_code")
        for key in (
            "width_px",
            "height_px",
            "rotation_degrees",
            "coordinate_space_version",
            "content_fingerprint",
            "derived_render_artifact_id",
        ):
            if key in values:
                setattr(row, key, values[key])
        row.updated_at = datetime.now(UTC)
        self.db.add(
            ScanCheckingEvent(
                batch_id=artifact.batch_id,
                aggregate_type="page",
                aggregate_id=row.id,
                event_type="page.status_changed",
                actor_user_id=values["actor_user_id"],
                details={"from": old, "to": row.status},
            )
        )
        await self.db.flush()
        return _page(row)

    async def commit(self):
        await self.db.commit()

    async def rollback(self):
        await self.db.rollback()
