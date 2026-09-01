"""Version-one strict HTTP contracts for explicit catalog proposals."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, strict=True)]


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SubjectProposalRequest(ProposalRequest):
    kind: Literal["subject"]
    name: Name


class GradeProposalRequest(ProposalRequest):
    kind: Literal["grade"]
    number: Annotated[int, Field(strict=True, ge=1, le=11)]
    name: Name


class TopicProposalRequest(ProposalRequest):
    kind: Literal["topic"]
    name: Name
    subject_id: UUID
    grade_id: UUID


class SubtopicProposalRequest(ProposalRequest):
    kind: Literal["subtopic"]
    name: Name
    topic_id: UUID


class SkillProposalRequest(ProposalRequest):
    kind: Literal["skill"]
    name: Name
    subtopic_id: UUID


CatalogProposalRequest = Annotated[
    SubjectProposalRequest | GradeProposalRequest | TopicProposalRequest | SubtopicProposalRequest | SkillProposalRequest,
    Field(discriminator="kind"),
]


class CatalogProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)
    kind: Literal["subject", "grade", "topic", "subtopic", "skill"]
    id: UUID
    name: str
    status: Literal["active", "provisional"]
    outcome: Literal["existing_active", "existing_provisional", "created_provisional"]
    number: int | None = None
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None
