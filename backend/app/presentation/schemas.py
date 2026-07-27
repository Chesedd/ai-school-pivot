"""HTTP-only Pydantic schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillLinkCreate(StrictRequest):
    skill_id: UUID
    weight: Decimal
    is_primary: bool


class InitialVersionCreate(StrictRequest):
    title: str | None = None
    statement: str
    task_type: Literal["test", "calculation", "problem", "open_question", "essay"]
    answer_format: Literal["single_choice", "multiple_choice", "short_text", "number", "expression", "long_text"]
    difficulty: Literal["basic", "standard", "advanced"]
    source: str | None = None
    skills: list[SkillLinkCreate] = Field(min_length=1)


class TaskCreateRequest(StrictRequest):
    subject_id: UUID
    grade_id: UUID
    topic_id: UUID
    subtopic_id: UUID | None = None
    initial_version: InitialVersionCreate


class SkillLinkResponse(BaseModel):
    id: UUID
    skill_id: UUID
    name: str
    weight: Decimal
    is_primary: bool


class TaskVersionResponse(BaseModel):
    id: UUID
    version_no: int
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: str
    source: str | None
    status: str
    created_by: UUID
    created_at: datetime
    skills: list[SkillLinkResponse]


class TaskResponse(BaseModel):
    id: UUID
    subject_id: UUID
    grade_id: UUID
    topic_id: UUID
    subtopic_id: UUID | None
    created_by: UUID
    created_at: datetime
    initial_version: TaskVersionResponse


class TaskListItemResponse(BaseModel):
    task_id: UUID
    subject_id: UUID
    subject_name: str
    grade_id: UUID
    grade_name: str
    topic_id: UUID
    topic_name: str
    subtopic_id: UUID | None
    subtopic_name: str | None
    latest_version_id: UUID
    version_no: int
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: str
    status: str
    primary_skill_id: UUID | None
    primary_skill_name: str | None
    created_at: datetime
    archived_at: datetime | None


class TaskListPageResponse(BaseModel):
    items: list[TaskListItemResponse]
    total: int
    offset: int
    limit: int


class CatalogRefResponse(BaseModel):
    id: UUID
    name: str


class TaskVersionSummaryResponse(BaseModel):
    id: UUID
    version_no: int
    status: str
    created_at: datetime
    approved_at: datetime | None


class TaskCardVersionResponse(BaseModel):
    id: UUID
    version_no: int
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: str
    source: str | None
    status: str
    skills: list[SkillLinkResponse]
    created_by: UUID
    created_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    methodology: "MethodologyResponse"


class ExpectedSolutionRequest(StrictRequest):
    solution_text: str
    final_answer: str | None = None
    solution_steps: list[str]

class RubricItemRequest(StrictRequest):
    criterion: str
    max_points: Decimal
    required: bool
    common_failure: str | None = None

class RubricRequest(StrictRequest):
    grading_mode: Literal["points"]
    notes: str | None = None
    items: list[RubricItemRequest]

class AcceptedAnswerRequest(StrictRequest):
    answer_value: str
    tolerance: Decimal | None = None
    unit: str | None = None
    normalization_rule: str | None = None

class TypicalErrorRequest(StrictRequest):
    skill_id: UUID
    code: str
    title: str
    description: str
    severity: Literal["low", "medium", "high"]
    remediation_hint: str | None = None
    detection_hint: str | None = None

class HintRequest(StrictRequest):
    level: int
    hint_text: str

class MethodologyPutRequest(StrictRequest):
    expected_solution: ExpectedSolutionRequest | None
    rubric: RubricRequest | None
    accepted_answers: list[AcceptedAnswerRequest]
    typical_errors: list[TypicalErrorRequest]
    hints: list[HintRequest]

class ExpectedSolutionResponse(BaseModel):
    id: UUID
    solution_text: str
    final_answer: str | None
    solution_steps: list[str]

class RubricItemResponse(BaseModel):
    id: UUID
    criterion: str
    max_points: Decimal
    required: bool
    common_failure: str | None
    order_index: int

class RubricResponse(BaseModel):
    id: UUID
    grading_mode: str
    max_score: Decimal
    notes: str | None
    items: list[RubricItemResponse]

class AcceptedAnswerResponse(BaseModel):
    id: UUID
    answer_value: str
    tolerance: Decimal | None
    unit: str | None
    normalization_rule: str | None

class TypicalErrorResponse(BaseModel):
    id: UUID
    skill_id: UUID
    code: str
    title: str
    description: str
    severity: str
    remediation_hint: str | None
    detection_hint: str | None

class HintResponse(BaseModel):
    id: UUID
    level: int
    hint_text: str

class MethodologyResponse(BaseModel):
    expected_solution: ExpectedSolutionResponse | None
    rubric: RubricResponse | None
    accepted_answers: list[AcceptedAnswerResponse]
    typical_errors: list[TypicalErrorResponse]
    hints: list[HintResponse]


class TaskCardResponse(BaseModel):
    id: UUID
    subject: CatalogRefResponse
    grade: CatalogRefResponse
    topic: CatalogRefResponse
    subtopic: CatalogRefResponse | None
    created_by: UUID
    created_at: datetime
    archived_at: datetime | None
    latest_version: TaskCardVersionResponse
    approved_version: TaskVersionSummaryResponse | None
    versions: list[TaskVersionSummaryResponse]


class CatalogItemResponse(BaseModel):
    id: UUID
    name: str
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None


class CatalogResponse(BaseModel):
    catalog: str
    items: list[CatalogItemResponse]
