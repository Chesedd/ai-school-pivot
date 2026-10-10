"""Human review contracts and deterministic approval of immutable paper evidence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from app.application.scan_checking_contracts import SemanticRegion, Token
from app.application.scan_grading_policy import (
    AcceptedItemScore,
    GradeOverrideCommand,
    ScanGradingPolicy,
    Score,
    calculate_grade,
    calculate_percentage,
    calculate_total,
)


class PaperReviewError(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(code)


class ReviewContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class PaperReviewItem(ReviewContract):
    assessment_item_id: UUID
    source_check_result_id: UUID
    status: Literal[
        "correct",
        "partially_correct",
        "incorrect",
        "not_attempted",
        "unreadable",
        "insufficient_evidence",
    ]
    accepted_score: Score | None
    max_score: Score
    teacher_comment: StrictStr | None = Field(
        default=None, min_length=1, max_length=2000
    )

    @model_validator(mode="after")
    def bounded_score(self):
        if not self.max_score.is_finite() or self.max_score <= 0:
            raise ValueError("invalid_max_score")
        if self.accepted_score is not None and (
            not self.accepted_score.is_finite() or self.accepted_score > self.max_score
        ):
            raise ValueError("accepted_score_out_of_range")
        return self


class PaperReviewFinding(ReviewContract):
    finding_id: UUID
    source_check_finding_id: UUID | None = None
    scan_page_id: UUID
    category: Token
    severity: Literal["info", "warning", "error"]
    short_explanation: StrictStr = Field(min_length=1, max_length=500)
    detailed_explanation: StrictStr | None = Field(
        default=None, min_length=1, max_length=4000
    )
    region: SemanticRegion
    coordinate_space_version: Literal["normalized_upright_v1"] = "normalized_upright_v1"

    @field_validator("short_explanation", "detailed_explanation")
    @classmethod
    def meaningful_text(cls, value):
        if value is not None and not value.strip():
            raise ValueError("empty_explanation")
        return value


def seed_review_item(
    *,
    assessment_item_id: UUID,
    source_check_result_id: UUID,
    status: str,
    suggested_score: Decimal | None,
    max_score: Decimal,
) -> PaperReviewItem:
    """Copy ordinary suggestions; indeterminate AI statuses remain unresolved."""
    return PaperReviewItem(
        assessment_item_id=assessment_item_id,
        source_check_result_id=source_check_result_id,
        status=status,
        accepted_score=suggested_score
        if status in {"correct", "partially_correct", "incorrect"}
        else None,
        max_score=max_score,
    )


class PaperReviewDraft(ReviewContract):
    schema_version: Literal["paper_check_review_draft.v1"] = (
        "paper_check_review_draft.v1"
    )
    paper_submission_id: UUID
    check_run_id: UUID
    ai_revision_id: UUID
    summary: StrictStr = Field(min_length=1, max_length=4000)
    items: tuple[PaperReviewItem, ...] = Field(min_length=1, max_length=256)
    findings: tuple[PaperReviewFinding, ...] = Field(default=(), max_length=16384)

    @model_validator(mode="after")
    def unique_identifiers(self):
        for values in (
            [x.assessment_item_id for x in self.items],
            [x.source_check_result_id for x in self.items],
            [x.finding_id for x in self.findings],
            [
                x.source_check_finding_id
                for x in self.findings
                if x.source_check_finding_id
            ],
        ):
            if len(values) != len(set(values)):
                raise ValueError("duplicate_review_identity")
        return self


class PaperReviewEdit(ReviewContract):
    expected_version: StrictInt = Field(gt=0)
    draft: PaperReviewDraft


class PaperReviewOverride(ReviewContract):
    override_grade: StrictStr = Field(min_length=1, max_length=64)
    reason: StrictStr = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_override(self):
        GradeOverrideCommand(
            calculated_grade="pending",
            override_grade=self.override_grade,
            reason=self.reason,
        )
        return self


class PaperReviewApproval(ReviewContract):
    expected_version: StrictInt = Field(gt=0)
    grade_override: PaperReviewOverride | None = None


class PaperReviewGrade(ReviewContract):
    calculated_total_score: Decimal
    total_max_score: Decimal
    calculated_percentage: Decimal
    calculated_grade: StrictStr = Field(min_length=1, max_length=64)
    final_grade: StrictStr = Field(min_length=1, max_length=64)
    grade_override_reason: StrictStr | None = Field(
        default=None, min_length=1, max_length=2000
    )


class PaperReviewRevision(ReviewContract):
    schema_version: Literal["paper_check_review_revision.v1"] = (
        "paper_check_review_revision.v1"
    )
    id: UUID
    review_id: UUID
    paper_submission_id: UUID
    check_run_id: UUID
    ai_revision_id: UUID
    revision_number: StrictInt = Field(ge=0)
    revision_kind: Literal["ai_seed", "human_edit", "approved"]
    snapshot: PaperReviewDraft
    actor_user_id: UUID
    grading_policy_revision: StrictInt = Field(gt=0)
    grading_policy_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    change_summary: tuple[
        Literal["summary", "item_score_status", "findings", "grade_override_intent"],
        ...,
    ]
    grade_result: PaperReviewGrade | None
    approved_at: datetime | None
    created_at: datetime


class PaperReviewResponse(ReviewContract):
    schema_version: Literal["paper_check_review_response.v1"] = (
        "paper_check_review_response.v1"
    )
    review_id: UUID
    paper_submission_id: UUID
    check_run_id: UUID
    ai_revision_id: UUID
    state: Literal["reviewing", "approved"]
    version: StrictInt = Field(gt=0)
    draft: PaperReviewDraft
    latest_approved_revision_id: UUID | None
    latest_approved_revision: PaperReviewRevision | None


def canonicalize_draft(
    draft: PaperReviewDraft, item_order: tuple[UUID, ...]
) -> PaperReviewDraft:
    """Frozen input position orders items; stable UUID orders semantic findings."""
    positions = {identifier: n for n, identifier in enumerate(item_order)}
    if set(positions) != {item.assessment_item_id for item in draft.items}:
        raise PaperReviewError("paper_review_item_coverage_mismatch")
    return draft.model_copy(
        update={
            "items": tuple(
                sorted(draft.items, key=lambda x: positions[x.assessment_item_id])
            ),
            "findings": tuple(sorted(draft.findings, key=lambda x: str(x.finding_id))),
        }
    )


def changed_domains(
    previous: PaperReviewDraft, current: PaperReviewDraft
) -> tuple[str, ...]:
    return tuple(
        label
        for field, label in (
            ("summary", "summary"),
            ("items", "item_score_status"),
            ("findings", "findings"),
        )
        if getattr(previous, field) != getattr(current, field)
    )


def approve_scores(
    policy: ScanGradingPolicy,
    draft: PaperReviewDraft,
    override: PaperReviewOverride | None = None,
) -> PaperReviewGrade:
    maxima = {x.assessment_item_id: x.max_score for x in policy.assessment_items}
    if set(maxima) != {x.assessment_item_id for x in draft.items}:
        raise PaperReviewError("paper_review_item_coverage_mismatch")
    scores = []
    for item in draft.items:
        if item.max_score != maxima[item.assessment_item_id]:
            raise PaperReviewError("paper_review_policy_mismatch")
        score = item.accepted_score
        if item.status in {"unreadable", "insufficient_evidence"} or score is None:
            raise PaperReviewError("paper_review_unresolved_item")
        valid = (
            (item.status == "correct" and score == item.max_score)
            or (item.status == "partially_correct" and 0 < score < item.max_score)
            or (item.status in {"incorrect", "not_attempted"} and score == 0)
        )
        if not valid:
            raise PaperReviewError("paper_review_status_score_mismatch")
        scores.append(
            AcceptedItemScore(assessment_item_id=item.assessment_item_id, score=score)
        )
    total = calculate_total(policy, tuple(scores))
    grade = calculate_grade(policy, total)
    command = (
        None
        if override is None
        else GradeOverrideCommand(
            calculated_grade=grade,
            override_grade=override.override_grade,
            reason=override.reason,
        )
    )
    return PaperReviewGrade(
        calculated_total_score=total,
        total_max_score=policy.total_max_score,
        calculated_percentage=calculate_percentage(policy, total),
        calculated_grade=grade,
        final_grade=command.override_grade if command else grade,
        grade_override_reason=command.reason if command else None,
    )
