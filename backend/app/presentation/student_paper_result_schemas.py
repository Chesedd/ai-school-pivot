"""Student navigation read model, independent of immutable teacher provenance."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.application.paper_result_publication import (
    PublishedFinding,
    PublishedGrade,
    PublishedItem,
)


class StudentPublishedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["paper_result_publication.v1"]
    paper_submission_id: UUID
    summary: str
    items: list[PublishedItem]
    findings: list[PublishedFinding]
    grade: PublishedGrade


class StudentPublishedPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scan_page_id: UUID
    page_order: int
    width: int
    height: int
    coordinate_space_version: Literal["normalized_upright_v1"]
    content_url: str


class StudentAssignmentPaperResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_id: UUID
    assignment_title: str
    id: UUID
    paper_submission_id: UUID
    publication_number: int
    published_at: datetime
    active: Literal[True]
    result: StudentPublishedResult
    pages: list[StudentPublishedPage]
