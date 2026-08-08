"""Strict HTTP schemas for Prompt 3 Assessment Core endpoints."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class AssessmentCreateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200, pattern=r"^\S(?:.*\S)?$")
    description: str | None = Field(default=None, max_length=4000)


class AssessmentPatchRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^\S(?:.*\S)?$")
    description: str | None = Field(default=None, max_length=4000)
    expected_updated_at: datetime

    @field_validator("expected_updated_at")
    @classmethod
    def timestamp_has_offset(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expected_updated_at должен содержать UTC offset.")
        return value

    @model_validator(mode="after")
    def has_change(self):
        if not (self.model_fields_set - {"expected_updated_at"}):
            raise ValueError("Должно быть передано хотя бы одно изменяемое поле.")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title не может быть null.")
        return self


class VariantCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^\S(?:.*\S)?$")


class AssessmentItemResponse(StrictModel):
    id: UUID
    task_version_id: UUID
    position: int
    points: Decimal


def _validate_points(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0 or value > Decimal("999999.99") or value.as_tuple().exponent < -2:
        raise ValueError("points должны быть от 0.01 до 999999.99 и содержать не более двух знаков после запятой.")
    return value


class AssessmentItemCreateRequest(StrictModel):
    task_version_id: UUID
    points: Decimal

    _points = field_validator("points")(_validate_points)


class AssessmentItemPatchRequest(StrictModel):
    points: Decimal
    expected_updated_at: datetime

    _points = field_validator("points")(_validate_points)
    _timestamp = field_validator("expected_updated_at")(AssessmentPatchRequest.timestamp_has_offset.__func__)


class AssessmentItemOrderRequest(StrictModel):
    item_ids: list[UUID]
    expected_updated_at: datetime

    _timestamp = field_validator("expected_updated_at")(AssessmentPatchRequest.timestamp_has_offset.__func__)

    @field_validator("item_ids")
    @classmethod
    def unique_item_ids(cls, value: list[UUID]):
        if len(value) != len(set(value)):
            raise ValueError("item_ids не должны повторяться.")
        return value


class VariantResponse(StrictModel):
    id: UUID
    name: str
    position: int
    items: list[AssessmentItemResponse] = []
    total_points: Decimal = Decimal("0.00")


class AssessmentResponse(StrictModel):
    id: UUID
    title: str
    description: str | None
    status: Literal["draft", "published"]
    variants: list[VariantResponse]
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    published_by: UUID | None


class AssessmentListItem(StrictModel):
    id: UUID
    title: str
    description: str | None
    status: Literal["draft", "published"]
    variant_count: int
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    published_by: UUID | None


class AssessmentListPage(StrictModel):
    items: list[AssessmentListItem]
    total: int
    offset: int
    limit: int


class PublishAssessmentRequest(StrictModel):
    class_group_id: UUID
    start_at: datetime
    due_at: datetime
    max_attempts: int = Field(ge=1, le=100)

    _start_offset = field_validator("start_at")(AssessmentPatchRequest.timestamp_has_offset.__func__)
    _due_offset = field_validator("due_at")(AssessmentPatchRequest.timestamp_has_offset.__func__)

    @model_validator(mode="after")
    def valid_window(self):
        if self.start_at >= self.due_at:
            raise ValueError("start_at должен быть раньше due_at.")
        return self


class EmptyRequest(StrictModel):
    pass


class AssignmentResponse(StrictModel):
    id: UUID
    assessment_id: UUID
    class_group_id: UUID
    status: Literal["open", "closed"]
    start_at: datetime
    due_at: datetime
    max_attempts: int
    created_at: datetime
    closed_at: datetime | None
    participant_count: int
    participant_ids: list[UUID]


class PublicationResponse(StrictModel):
    assessment: AssessmentResponse
    assignment: AssignmentResponse
