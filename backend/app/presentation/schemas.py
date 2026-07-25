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
