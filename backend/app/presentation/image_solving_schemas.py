"""Public, disclosure-safe DTOs for the image-solving HTTP boundary."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


class ImageSolvingDto(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class CreateImageSolvingSessionRequest(ImageSolvingDto):
    # JSON has no UUID scalar, so permit Pydantic to parse this HTTP-boundary
    # field while retaining strict validation for every other DTO field.
    artifact_id: UUID = Field(strict=False)


class ImageSolvingSessionResponse(ImageSolvingDto):
    session_id: UUID
    artifact_id: UUID
    status: StrictStr


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
