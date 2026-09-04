"""Public, disclosure-safe DTOs for the image-solving HTTP boundary."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

HttpUuid = Annotated[UUID, Field(strict=False)]


class ImageSolvingDto(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class CreateImageSolvingSessionRequest(ImageSolvingDto):
    # JSON has no UUID scalar, so permit Pydantic to parse this HTTP-boundary
    # field while retaining strict validation for every other DTO field.
    artifact_id: UUID = Field(strict=False)
    solution_instruction: StrictStr | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("solution_instruction")
    @classmethod
    def normalized_instruction(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("whitespace_not_normalized")
        return value


class ImageSolvingSessionResponse(ImageSolvingDto):
    session_id: UUID
    artifact_id: UUID
    status: StrictStr
    solution_instruction: StrictStr | None = None
    failure_code: StrictStr | None = None
    failure_stage: StrictStr | None = None


class StageStatusResponse(ImageSolvingDto):
    extraction: StrictStr
    solver: StrictStr
    validation: StrictStr


class ImageSolvingStateResponse(ImageSolvingSessionResponse):
    stages: StageStatusResponse
    created_at: datetime
    updated_at: datetime


class TaskClassificationResponse(ImageSolvingDto):
    task_type: StrictStr | None
    answer_format: StrictStr | None


class ExtractionResponse(ImageSolvingDto):
    extracted_text: StrictStr
    structured_statement: StrictStr
    task_classification: TaskClassificationResponse
    confidence: Decimal
    choices: tuple[StrictStr, ...] | None = None
    metadata: "ExtractionMetadataResponse"


class ExtractionMetadataResponse(ImageSolvingDto):
    title: StrictStr
    subject: StrictStr
    grade: StrictInt
    topic: StrictStr
    subtopic: StrictStr | None
    skills: tuple[StrictStr, ...]
    task_type: StrictStr
    answer_format: StrictStr
    difficulty: StrictInt
    tags: tuple[StrictStr, ...]


class SolutionResponse(ImageSolvingDto):
    answer: StrictStr
    reasoning_summary: StrictStr
    confidence: Decimal


class ValidationResponse(ImageSolvingDto):
    status: StrictStr
    findings: tuple[StrictStr, ...]
    manual_review: StrictBool


class ImageSolvingResultResponse(ImageSolvingDto):
    session_id: UUID
    artifact_id: UUID
    extraction: ExtractionResponse
    solution: SolutionResponse
    validation: ValidationResponse


class PromoteImageSolvingRequest(ImageSolvingDto):
    title: StrictStr | None = Field(default=None, max_length=500)
    statement: StrictStr = Field(min_length=1, max_length=30_000)
    task_type: Literal["test", "calculation", "problem", "open_question", "essay"]
    answer_format: Literal["single_choice", "multiple_choice", "short_text", "number", "expression", "long_text"]
    difficulty: StrictInt = Field(ge=1, le=100)
    subject_id: UUID = Field(strict=False)
    grade_id: UUID = Field(strict=False)
    topic_id: UUID = Field(strict=False)
    subtopic_id: UUID | None = Field(default=None, strict=False)
    skill_ids: tuple[HttpUuid, ...] = Field(strict=False, min_length=1, max_length=20)
    tag_ids: tuple[HttpUuid, ...] = Field(strict=False, default=(), max_length=8)
    solution: StrictStr = Field(min_length=1, max_length=30_000)
    final_answer: StrictStr | None = Field(default=None, max_length=4_000)
    review_confirmed: StrictBool
    review_note: StrictStr | None = Field(default=None, max_length=4_000)
    confirm_questionable: StrictBool = False
    alias_confirmations: tuple["CatalogAliasConfirmation", ...] = Field(
        strict=False, default=(), max_length=8)

    @field_validator("title", "statement", "solution", "final_answer", "review_note")
    @classmethod
    def clean(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("whitespace_not_normalized")
        return value

    @field_validator("skill_ids")
    @classmethod
    def unique_skills(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate_skills")
        return value

    @field_validator("tag_ids")
    @classmethod
    def unique_tags(cls,value:tuple[UUID,...])->tuple[UUID,...]:
        if len(value)!=len(set(value)):raise ValueError("duplicate_tags")
        return value


class PromoteImageSolvingResponse(ImageSolvingDto):
    session_id: UUID
    task_id: UUID
    task_version_id: UUID
    status: Literal["draft"]
    already_existing: StrictBool


class CatalogAliasConfirmation(ImageSolvingDto):
    kind: Literal["subject", "topic", "subtopic", "skill"]
    recognized_label: StrictStr = Field(min_length=1, max_length=200)
    target_id: UUID = Field(strict=False)


class AttemptUsageResponse(ImageSolvingDto):
    input_tokens: StrictInt | None
    output_tokens: StrictInt | None


class ImageSolvingAttemptResponse(ImageSolvingDto):
    stage: Literal["extraction", "solver", "validation"]
    provider: StrictStr | None
    model: StrictStr | None
    usage: AttemptUsageResponse
    cost: Decimal | None
    currency: StrictStr | None
    latency_ms: StrictInt | None
    request_id: StrictStr | None
    created_at: datetime


class ImageSolvingAttemptsResponse(ImageSolvingDto):
    items: tuple[ImageSolvingAttemptResponse, ...]
