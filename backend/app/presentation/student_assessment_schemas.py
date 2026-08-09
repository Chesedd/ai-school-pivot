from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from pydantic import field_validator
from app.presentation.assessment_schemas import StrictModel, EmptyRequest

class AnswerPutRequest(StrictModel):
    raw_answer: Any
    expected_updated_at: datetime | None = None

    @field_validator("expected_updated_at")
    @classmethod
    def timestamp_has_offset(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expected_updated_at должен содержать UTC offset.")
        return value

class StudentAnswerResponse(StrictModel):
    item_id: UUID
    raw_answer: Any
    normalized_answer: Any
    created_at: datetime
    updated_at: datetime

class ExecutionItemResponse(StrictModel):
    id: UUID; task_version_id: UUID; position: int; points: Decimal
    title: str | None; statement: str; task_type: str; answer_format: str

class SubmissionResponse(StrictModel):
    id: UUID; attempt_no: int; status: Literal["draft", "submitted"]
    assigned_variant_id: UUID; resumed: bool = False
    started_at: datetime; submitted_at: datetime | None
    answers: list[StudentAnswerResponse]; items: list[ExecutionItemResponse]

class StudentAssignmentSummary(StrictModel):
    assignment_id: UUID; assessment_id: UUID; title: str; status: Literal["open", "closed"]
    start_at: datetime; due_at: datetime; max_attempts: int; assigned_variant_id: UUID | None
    attempt_count: int

class StudentAssignmentPage(StrictModel):
    items: list[StudentAssignmentSummary]; total: int; offset: int; limit: int

class StudentAssignmentDetail(StudentAssignmentSummary):
    description: str | None; participant_id: UUID; current_draft_attempt_id: UUID | None
    submitted_attempt_count: int
