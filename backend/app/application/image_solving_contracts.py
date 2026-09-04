"""Immutable provider-neutral contracts for solving tasks from images.

These DTOs deliberately contain no pipeline behaviour.  They describe the four
artifacts that can cross future extraction, solving, and validation boundaries.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from app.application.authoring import canonical_json_bytes
from app.application.content_bank import AnswerFormatValue, TaskTypeValue

MAX_ARTIFACT_ID = 128
MAX_MIME_TYPE = 127
MAX_USER_CONTEXT = 4_000
MAX_EXTRACTED_TEXT = 30_000
MAX_STRUCTURED_STATEMENT = 30_000
MAX_CLASSIFIER_VALUE = 128
MAX_CHOICE = 2_000
MAX_CHOICES = 32
MAX_REASONING_SUMMARY = 30_000
MAX_FINAL_ANSWER = 4_000
MAX_STATUS = 64
MAX_ISSUE = 2_000
MAX_ISSUES = 32
MAX_FINDING = 2_000
MAX_FINDINGS = 32
MAX_METADATA_NAME = 500

Confidence = Annotated[Decimal, Field(strict=True, ge=Decimal("0"), le=Decimal("1"))]


class _ContractV1(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _clean(value: str, *, error: str = "invalid_contract") -> str:
    if value != value.strip():
        raise ValueError(error)
    return value


class InputArtifactV1(_ContractV1):
    artifact_id: StrictStr = Field(min_length=1, max_length=MAX_ARTIFACT_ID)
    mime_type: StrictStr = Field(
        min_length=3,
        max_length=MAX_MIME_TYPE,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$",
    )
    content_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    user_context: StrictStr = Field(min_length=1, max_length=MAX_USER_CONTEXT)

    @field_validator("artifact_id", "user_context")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _clean(value)


class ExtractionMetadataV1(_ContractV1):
    """Semantic Content Bank hints produced without knowledge of catalog IDs."""

    title: StrictStr = Field(min_length=1, max_length=MAX_METADATA_NAME)
    subject: StrictStr = Field(min_length=1, max_length=200)
    grade: StrictInt = Field(ge=1, le=11)
    topic: StrictStr = Field(min_length=1, max_length=200)
    subtopic: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    skills: tuple[StrictStr, ...] = Field(min_length=1, max_length=5)
    task_type: TaskTypeValue
    answer_format: AnswerFormatValue
    difficulty: StrictInt = Field(ge=1, le=100)
    tags: tuple[StrictStr, ...] = Field(default=(), max_length=8)

    @field_validator("title", "subject", "topic", "subtopic")
    @classmethod
    def clean_metadata_text(cls, value: str | None) -> str | None:
        return None if value is None else _clean(value)

    @field_validator("skills", "tags")
    @classmethod
    def clean_metadata_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > 200 or item != item.strip() for item in value):
            raise ValueError("invalid_contract")
        if len({_normalized_metadata_name(item) for item in value}) != len(value):
            raise ValueError("duplicate_metadata_name")
        return value

def _normalized_metadata_name(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


class ExtractionResultV1(_ContractV1):
    extracted_text: StrictStr = Field(min_length=1, max_length=MAX_EXTRACTED_TEXT)
    structured_statement: StrictStr = Field(min_length=1, max_length=MAX_STRUCTURED_STATEMENT)
    detected_task_type: StrictStr | None = Field(default=None, min_length=1, max_length=MAX_CLASSIFIER_VALUE)
    detected_answer_format: StrictStr | None = Field(default=None, min_length=1, max_length=MAX_CLASSIFIER_VALUE)
    choices: tuple[StrictStr, ...] | None = Field(default=None, min_length=1, max_length=MAX_CHOICES)
    extraction_confidence: Confidence
    ocr_issues: tuple[StrictStr, ...] = Field(max_length=MAX_ISSUES)
    metadata: ExtractionMetadataV1

    @field_validator("extracted_text", "structured_statement", "detected_task_type", "detected_answer_format")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return None if value is None else _clean(value)

    @field_validator("choices")
    @classmethod
    def clean_choices(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and (any(not item or len(item) > MAX_CHOICE or item != item.strip() for item in value)
                                  or len(set(value)) != len(value)):
            raise ValueError("invalid_contract")
        return value

    @field_validator("ocr_issues")
    @classmethod
    def clean_issues(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > MAX_ISSUE or item != item.strip() for item in value):
            raise ValueError("invalid_contract")
        return value


class SolverResultV1(_ContractV1):
    # There is currently one end-to-end solver outcome.  Keeping it literal makes
    # the forced-tool schema tell providers which machine value to emit.
    status: Literal["solved"]
    reasoning_summary: StrictStr = Field(min_length=1, max_length=MAX_REASONING_SUMMARY)
    final_answer: StrictStr = Field(min_length=1, max_length=MAX_FINAL_ANSWER)
    confidence: Confidence

    @field_validator("reasoning_summary", "final_answer")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _clean(value)


# Backward-compatible public name used by the Phase 4A extraction-first slice.
SolutionResultV1 = SolverResultV1


class ValidationStatus(StrEnum):
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class ImageSolvingStatus(StrEnum):
    CREATED = "created"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    SOLVING = "solving"
    SOLVED = "solved"
    VALIDATED = "validated"
    FAILED = "failed"


class ValidationResultV1(_ContractV1):
    validation_status: Literal["validated", "needs_review", "failed"]
    confidence: Confidence
    findings: tuple[StrictStr, ...] = Field(max_length=MAX_FINDINGS)
    requires_human_review: StrictBool
    extraction_confidence_check: StrictBool = False
    OCR_quality_check: StrictBool = False
    solver_status_check: StrictBool = False
    answer_consistency_check: StrictBool = False

    @field_validator("validation_status")
    @classmethod
    def clean_status(cls, value: str) -> str:
        return _clean(value)

    @field_validator("findings")
    @classmethod
    def clean_findings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > MAX_FINDING or item != item.strip() for item in value):
            raise ValueError("invalid_contract")
        return value


class ImageSolvingSession(BaseModel):
    """Application aggregate, intentionally independent of authoring sessions."""
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    session_id: UUID
    owner_id: UUID
    input_artifact_id: UUID
    solution_instruction: StrictStr | None = Field(default=None, max_length=4000)
    extraction_checkpoint: ExtractionResultV1 | None = None
    solver_checkpoint: SolverResultV1 | None = None
    validation_checkpoint: ValidationResultV1 | None = None
    lifecycle_status: ImageSolvingStatus
    failure_code: StrictStr | None = None
    created_at: datetime
    updated_at: datetime
