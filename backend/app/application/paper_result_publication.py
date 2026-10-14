"""Explicit publication contracts; student material is an allowlisted frozen copy."""

import hashlib
import json
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.application.paper_checking_review import (
    PaperReviewRevision,
    ReviewContract,
    approve_scores,
)
from app.application.scan_checking_contracts import SemanticRegion, Token
from app.application.scan_grading_policy import ScanGradingPolicy


class PaperPublicationError(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(code)


class PublishPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved_review_revision_id: UUID


class PublicationEntry(PublishPaper):
    paper_submission_id: UUID


class SelectedPublications(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[PublicationEntry] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_papers(self):
        if len({x.paper_submission_id for x in self.entries}) != len(self.entries):
            raise ValueError("duplicate_paper_selection")
        return self


class WithdrawPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def normalized_reason(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("paper_publication_withdrawal_reason_required")
        return value


class WithdrawalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_submission_id: UUID
    publication_id: UUID


class SelectedWithdrawals(WithdrawPublication):
    entries: list[WithdrawalEntry] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_papers(self):
        if len({x.paper_submission_id for x in self.entries}) != len(self.entries):
            raise ValueError("duplicate_paper_selection")
        return self


class PublishedItem(ReviewContract):
    assessment_item_id: UUID
    status: Literal["correct", "partially_correct", "incorrect", "not_attempted"]
    accepted_score: Decimal = Field(ge=0)
    max_score: Decimal = Field(gt=0)
    teacher_comment: str | None = Field(default=None, min_length=1, max_length=2000)


class PublishedFinding(ReviewContract):
    assessment_item_id: UUID | None = None
    finding_id: UUID
    scan_page_id: UUID
    category: Token
    severity: Literal["info", "warning", "error"]
    short_explanation: str = Field(min_length=1, max_length=500)
    detailed_explanation: str | None = Field(
        default=None, min_length=1, max_length=4000
    )
    region: SemanticRegion
    coordinate_space_version: Literal["normalized_upright_v1"]


class PublishedGrade(ReviewContract):
    calculated_total_score: Decimal = Field(ge=0)
    total_max_score: Decimal = Field(gt=0)
    calculated_percentage: Decimal = Field(ge=0, le=100)
    calculated_grade: str = Field(min_length=1, max_length=64)
    final_grade: str = Field(min_length=1, max_length=64)


class PublicationSnapshot(ReviewContract):
    schema_version: Literal["paper_result_publication.v1"] = (
        "paper_result_publication.v1"
    )
    paper_submission_id: UUID
    approved_review_revision_id: UUID
    summary: str = Field(min_length=1, max_length=4000)
    items: tuple[PublishedItem, ...] = Field(min_length=1, max_length=256)
    findings: tuple[PublishedFinding, ...] = Field(max_length=16384)
    grade: PublishedGrade


def build_publication_snapshot(
    revision: PaperReviewRevision, policy: ScanGradingPolicy, page_ids: set[UUID]
) -> PublicationSnapshot:
    if revision.revision_kind != "approved":
        raise PaperPublicationError("paper_publication_revision_not_approved")
    draft = revision.snapshot
    if (
        revision.source_kind != draft.source_kind
        or revision.grade_result is None
        or revision.approved_at is None
        or (
            revision.paper_submission_id,
            revision.check_run_id,
            revision.ai_revision_id,
        )
        != (draft.paper_submission_id, draft.check_run_id, draft.ai_revision_id)
        or revision.grading_policy_fingerprint != policy.fingerprint
    ):
        raise PaperPublicationError("paper_publication_revision_mismatch")
    calculated = approve_scores(policy, draft)
    grade = revision.grade_result
    if any(
        getattr(calculated, name) != getattr(grade, name)
        for name in (
            "calculated_total_score",
            "total_max_score",
            "calculated_percentage",
            "calculated_grade",
        )
    ) or (
        grade.grade_override_reason is None
        and grade.final_grade != grade.calculated_grade
    ):
        raise PaperPublicationError("paper_publication_invalid_grade")
    if any(x.scan_page_id not in page_ids for x in draft.findings):
        raise PaperPublicationError("paper_publication_foreign_page")
    return PublicationSnapshot(
        paper_submission_id=revision.paper_submission_id,
        approved_review_revision_id=revision.id,
        summary=draft.summary,
        items=tuple(
            PublishedItem(**x.model_dump(exclude={"source_check_result_id"}))
            for x in draft.items
        ),
        findings=tuple(
            PublishedFinding(**x.model_dump(exclude={"source_check_finding_id"}))
            for x in sorted(draft.findings, key=lambda x: str(x.finding_id))
        ),
        grade=PublishedGrade(**grade.model_dump(exclude={"grade_override_reason"})),
    )


def publication_command_fingerprint(kind, entries=(), *, batch_id=None, reason=None):
    """UUID-sorted results make selection order immaterial; selectors are explicit."""
    payload = {
        "kind": kind,
        "batch_id": str(batch_id) if batch_id is not None else None,
        "entries": sorted(
            (entry.model_dump(mode="json") for entry in entries),
            key=lambda entry: entry["paper_submission_id"],
        ),
        "reason": reason,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_publication_request_key(key):
    if not isinstance(key, str) or not 1 <= len(key) <= 128 or not key.strip():
        raise PaperPublicationError("paper_publication_request_key_invalid")
    return key
