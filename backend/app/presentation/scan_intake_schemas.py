from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ScanBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=10_000)


class CancelScanBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


class BatchResponse(BaseModel):
    id: UUID
    assignment_id: UUID
    status: str
    instruction: str
    created_at: datetime
    updated_at: datetime


class ArtifactResponse(BaseModel):
    id: UUID
    upload_position: int
    original_filename: str
    mime_type: str
    size_bytes: int
    extraction_status: str
    page_count: int | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


class PageResponse(BaseModel):
    id: UUID
    source_artifact_id: UUID
    source_page_index: int
    width_px: int | None
    height_px: int | None
    status: str
    failure_code: str | None
    content_fingerprint: str | None
    content_url: str | None


class GroupingPageAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_participant_id: UUID | None
    expected_revision: int = Field(gt=0)
    expected_row_version: int = Field(gt=0)


class GroupingPageOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_ids: list[UUID] = Field(min_length=1)
    expected_revision: int = Field(gt=0)
    expected_row_version: int = Field(gt=0)


class GroupingConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(gt=0)
    expected_row_version: int = Field(gt=0)
