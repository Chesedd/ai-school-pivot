"""Application boundary and pure rules for durable scan intake."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Any, Protocol
from uuid import UUID

from app.application.scan_checking_contracts import (
    AssessmentScanBatchState,
    NORMALIZED_UPRIGHT_V1,
    validate_batch_transition,
)

MAX_INSTRUCTION_LENGTH = 10_000
MAX_CANCELLATION_REASON_LENGTH = 500
MAX_FILENAME_LENGTH = 255
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ScanIntakeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ArtifactExtractionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


class ScanPageStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


ARTIFACT_TRANSITIONS = {
    ArtifactExtractionStatus.PENDING: {ArtifactExtractionStatus.RUNNING},
    ArtifactExtractionStatus.RUNNING: {
        ArtifactExtractionStatus.COMPLETED,
        ArtifactExtractionStatus.FAILED_RETRYABLE,
        ArtifactExtractionStatus.FAILED_TERMINAL,
    },
    ArtifactExtractionStatus.FAILED_RETRYABLE: {ArtifactExtractionStatus.RUNNING},
    ArtifactExtractionStatus.COMPLETED: set(),
    ArtifactExtractionStatus.FAILED_TERMINAL: set(),
}
PAGE_TRANSITIONS = {
    ScanPageStatus.PENDING: {
        ScanPageStatus.READY,
        ScanPageStatus.FAILED_RETRYABLE,
        ScanPageStatus.FAILED_TERMINAL,
    },
    ScanPageStatus.FAILED_RETRYABLE: {
        ScanPageStatus.READY,
        ScanPageStatus.FAILED_TERMINAL,
    },
    ScanPageStatus.READY: set(),
    ScanPageStatus.FAILED_TERMINAL: set(),
}


@dataclass(frozen=True, slots=True)
class AssessmentScanBatchRecord:
    id: UUID
    assignment_id: UUID
    class_group_id: UUID
    created_by_user_id: UUID
    status: AssessmentScanBatchState
    instruction_text: str
    policy_draft: Any | None
    policy_snapshot: Any | None
    policy_schema_version: str | None
    policy_fingerprint: str | None
    prompt_policy_version: str | None
    matching_revision: int
    row_version: int
    request_key: str
    request_hash: str
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None
    cancelled_by_user_id: UUID | None
    cancellation_reason: str | None


@dataclass(frozen=True, slots=True)
class ScanBatchArtifactRecord:
    id: UUID
    batch_id: UUID
    input_artifact_id: UUID
    upload_position: int
    original_filename: str
    extraction_status: ArtifactExtractionStatus
    page_count: int | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ScanPageRecord:
    id: UUID
    batch_artifact_id: UUID
    source_page_index: int
    derived_render_artifact_id: UUID | None
    width_px: int | None
    height_px: int | None
    rotation_degrees: int
    coordinate_space_version: str | None
    content_fingerprint: str | None
    status: ScanPageStatus
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


class ScanIntakeRepository(Protocol):
    async def assignment_class(self, assignment_id: UUID) -> UUID | None: ...
    async def get_batch_by_request(
        self, actor_id: UUID, request_key: str
    ) -> AssessmentScanBatchRecord | None: ...
    async def create_batch(self, **values: Any) -> AssessmentScanBatchRecord: ...
    async def get_batch(
        self, batch_id: UUID, *, lock: bool = False
    ) -> AssessmentScanBatchRecord | None: ...
    async def list_batches(
        self, assignment_id: UUID
    ) -> tuple[AssessmentScanBatchRecord, ...]: ...
    async def cancel_batch(
        self, batch_id: UUID, actor_id: UUID, reason: str, expected_version: int
    ) -> AssessmentScanBatchRecord: ...
    async def attach_artifact(self, **values: Any) -> ScanBatchArtifactRecord: ...
    async def create_page(self, **values: Any) -> ScanPageRecord: ...
    async def transition_batch(self, **values: Any) -> AssessmentScanBatchRecord: ...
    async def transition_artifact(self, **values: Any) -> ScanBatchArtifactRecord: ...
    async def transition_page(self, **values: Any) -> ScanPageRecord: ...
    async def commit(self) -> None: ...


class ScanIntakeAccessPolicy(Protocol):
    async def can_access_class(self, class_group_id: UUID, actor_id: UUID) -> bool: ...


def canonical_request_hash(assignment_id: UUID, instruction_text: str) -> str:
    payload = json.dumps(
        {"assignment_id": str(assignment_id), "instruction_text": instruction_text},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def validate_event_details(details: dict[str, Any]) -> None:
    forbidden = {
        "ocr_text",
        "scan_bytes",
        "student_name",
        "student_names",
        "student_id",
        "participant_id",
        "teacher_instruction",
        "instruction_text",
        "storage_reference",
        "raw_provider_output",
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).casefold() in forbidden:
                    raise ScanIntakeError("unsafe_event_details")
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(details)


def validate_artifact_transition(
    current: ArtifactExtractionStatus, target: ArtifactExtractionStatus
) -> None:
    if target not in ARTIFACT_TRANSITIONS[current]:
        raise ScanIntakeError("invalid_artifact_transition")


def validate_page_transition(current: ScanPageStatus, target: ScanPageStatus) -> None:
    if target not in PAGE_TRANSITIONS[current]:
        raise ScanIntakeError("invalid_page_transition")


def validate_page_metadata(
    *,
    source_page_index: int,
    width_px: int | None,
    height_px: int | None,
    rotation_degrees: int,
    coordinate_space_version: str | None,
    content_fingerprint: str | None,
    status: ScanPageStatus,
) -> None:
    if type(source_page_index) is not int or source_page_index < 0:
        raise ScanIntakeError("invalid_page_index")
    if rotation_degrees not in (0, 90, 180, 270):
        raise ScanIntakeError("invalid_page_rotation")
    if (
        width_px is not None
        and (type(width_px) is not int or width_px <= 0)
        or height_px is not None
        and (type(height_px) is not int or height_px <= 0)
    ):
        raise ScanIntakeError("invalid_page_dimensions")
    if content_fingerprint is not None and not _SHA256.fullmatch(content_fingerprint):
        raise ScanIntakeError("invalid_content_fingerprint")
    if status is ScanPageStatus.READY and (
        not width_px
        or not height_px
        or coordinate_space_version != NORMALIZED_UPRIGHT_V1
        or content_fingerprint is None
    ):
        raise ScanIntakeError("invalid_ready_page")


class ScanIntakeService:
    def __init__(
        self, repository: ScanIntakeRepository, access_policy: ScanIntakeAccessPolicy
    ):
        self.repository = repository
        self.access_policy = access_policy

    async def create_batch(
        self,
        *,
        assignment_id: UUID,
        instruction_text: str,
        request_key: str,
        actor_user_id: UUID,
    ) -> AssessmentScanBatchRecord:
        instruction_text = _trimmed(
            instruction_text, MAX_INSTRUCTION_LENGTH, "invalid_instruction"
        )
        request_key = _trimmed(request_key, 128, "invalid_request_key")
        request_hash = canonical_request_hash(assignment_id, instruction_text)
        existing = await self.repository.get_batch_by_request(
            actor_user_id, request_key
        )
        if existing:
            if existing.request_hash != request_hash:
                raise ScanIntakeError("idempotency_conflict")
            return existing
        class_id = await self.repository.assignment_class(assignment_id)
        if class_id is None:
            raise ScanIntakeError("assignment_not_found")
        if not await self.access_policy.can_access_class(class_id, actor_user_id):
            raise ScanIntakeError("scan_batch_not_found")
        row = await self.repository.create_batch(
            assignment_id=assignment_id,
            class_group_id=class_id,
            created_by_user_id=actor_user_id,
            instruction_text=instruction_text,
            request_key=request_key,
            request_hash=request_hash,
        )
        await self.repository.commit()
        return row

    async def get_batch(
        self, batch_id: UUID, actor_user_id: UUID
    ) -> AssessmentScanBatchRecord:
        row = await self.repository.get_batch(batch_id)
        if row is None or not await self.access_policy.can_access_class(
            row.class_group_id, actor_user_id
        ):
            raise ScanIntakeError("scan_batch_not_found")
        return row

    async def list_batches(self, assignment_id: UUID, actor_user_id: UUID):
        class_id = await self.repository.assignment_class(assignment_id)
        if class_id is None or not await self.access_policy.can_access_class(
            class_id, actor_user_id
        ):
            raise ScanIntakeError("assignment_not_found")
        return await self.repository.list_batches(assignment_id)

    async def cancel_batch(
        self, *, batch_id: UUID, actor_user_id: UUID, reason: str
    ) -> AssessmentScanBatchRecord:
        reason = _trimmed(
            reason, MAX_CANCELLATION_REASON_LENGTH, "invalid_cancellation_reason"
        )
        row = await self.get_batch(batch_id, actor_user_id)
        if row.status is AssessmentScanBatchState.CANCELLED:
            return row
        try:
            validate_batch_transition(row.status, AssessmentScanBatchState.CANCELLED)
        except ValueError as exc:
            raise ScanIntakeError("invalid_batch_transition") from exc
        result = await self.repository.cancel_batch(
            batch_id, actor_user_id, reason, row.row_version
        )
        await self.repository.commit()
        return result

    async def attach_artifact(
        self,
        *,
        batch_id: UUID,
        input_artifact_id: UUID,
        upload_position: int,
        original_filename: str,
        actor_user_id: UUID,
    ) -> ScanBatchArtifactRecord:
        row = await self.get_batch(batch_id, actor_user_id)
        if row.status is AssessmentScanBatchState.CANCELLED:
            raise ScanIntakeError("batch_cancelled")
        if row.status not in (
            AssessmentScanBatchState.DRAFT,
            AssessmentScanBatchState.UPLOADING,
        ):
            raise ScanIntakeError("artifact_attachment_not_allowed")
        if type(upload_position) is not int or upload_position < 0:
            raise ScanIntakeError("invalid_upload_position")
        filename = _filename(original_filename)
        result = await self.repository.attach_artifact(
            batch_id=batch_id,
            input_artifact_id=input_artifact_id,
            upload_position=upload_position,
            original_filename=filename,
            actor_user_id=actor_user_id,
        )
        await self.repository.commit()
        return result

    async def create_page(
        self, *, actor_user_id: UUID, **values: Any
    ) -> ScanPageRecord:
        values.setdefault("rotation_degrees", 0)
        values.setdefault("width_px", None)
        values.setdefault("height_px", None)
        values.setdefault("coordinate_space_version", None)
        values.setdefault("content_fingerprint", None)
        values.setdefault("derived_render_artifact_id", None)
        values["status"] = ScanPageStatus(values.get("status", ScanPageStatus.PENDING))
        validate_page_metadata(
            **{
                k: values.get(k)
                for k in (
                    "source_page_index",
                    "width_px",
                    "height_px",
                    "rotation_degrees",
                    "coordinate_space_version",
                    "content_fingerprint",
                    "status",
                )
            }
        )
        result = await self.repository.create_page(
            actor_user_id=actor_user_id, **values
        )
        await self.repository.commit()
        return result

    async def transition_batch(
        self, *, batch_id: UUID, target: AssessmentScanBatchState, actor_user_id: UUID
    ) -> AssessmentScanBatchRecord:
        row = await self.get_batch(batch_id, actor_user_id)
        try:
            validate_batch_transition(row.status, target)
        except ValueError as exc:
            raise ScanIntakeError("invalid_batch_transition") from exc
        result = await self.repository.transition_batch(
            batch_id=batch_id,
            target=target,
            actor_user_id=actor_user_id,
            expected_version=row.row_version,
        )
        await self.repository.commit()
        return result

    async def transition_artifact(
        self,
        *,
        artifact_id: UUID,
        current: ArtifactExtractionStatus,
        target: ArtifactExtractionStatus,
        actor_user_id: UUID,
        page_count: int | None = None,
        failure_code: str | None = None,
    ) -> ScanBatchArtifactRecord:
        validate_artifact_transition(current, target)
        if target is ArtifactExtractionStatus.COMPLETED and (
            page_count is None or page_count < 0
        ):
            raise ScanIntakeError("completed_requires_page_count")
        if (
            target
            in {
                ArtifactExtractionStatus.FAILED_RETRYABLE,
                ArtifactExtractionStatus.FAILED_TERMINAL,
            }
        ) != bool(failure_code):
            raise ScanIntakeError("invalid_failure_code")
        result = await self.repository.transition_artifact(
            artifact_id=artifact_id,
            current=current,
            target=target,
            actor_user_id=actor_user_id,
            page_count=page_count,
            failure_code=failure_code,
        )
        await self.repository.commit()
        return result

    async def transition_page(
        self,
        *,
        page_id: UUID,
        current: ScanPageStatus,
        target: ScanPageStatus,
        actor_user_id: UUID,
        failure_code: str | None = None,
        **metadata: Any,
    ) -> ScanPageRecord:
        validate_page_transition(current, target)
        if (
            target in {ScanPageStatus.FAILED_RETRYABLE, ScanPageStatus.FAILED_TERMINAL}
        ) != bool(failure_code):
            raise ScanIntakeError("invalid_failure_code")
        if target is ScanPageStatus.READY:
            validate_page_metadata(
                source_page_index=0,
                rotation_degrees=metadata.get("rotation_degrees", 0),
                status=target,
                **{
                    k: metadata.get(k)
                    for k in (
                        "width_px",
                        "height_px",
                        "coordinate_space_version",
                        "content_fingerprint",
                    )
                },
            )
        result = await self.repository.transition_page(
            page_id=page_id,
            current=current,
            target=target,
            actor_user_id=actor_user_id,
            failure_code=failure_code,
            **metadata,
        )
        await self.repository.commit()
        return result


def _trimmed(value: str, maximum: int, code: str) -> str:
    if type(value) is not str:
        raise ScanIntakeError(code)
    value = value.strip()
    if not value or len(value) > maximum:
        raise ScanIntakeError(code)
    return value


def _filename(value: str) -> str:
    value = _trimmed(value, MAX_FILENAME_LENGTH, "invalid_filename")
    if "\x00" in value or PurePath(value).name != value or value in (".", ".."):
        raise ScanIntakeError("invalid_filename")
    return value


# Original PDFs remain immutable teacher-owned inputs. Future students receive only
# page-level content belonging to their published PaperSubmission.
