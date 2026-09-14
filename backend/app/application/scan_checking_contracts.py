"""Domain/application boundaries for scanned-paper intake and checking.

``AssessmentScanBatch`` owns intake, its frozen policy, and matching/grouping.
Future ``PaperSubmission`` objects represent physical work separately from digital
attempts. Matching can see a minimal roster; checking is a distinct anonymous
provider call. AI results are immutable evidence, while totals, grades, teacher
revisions, approval, and publication remain deterministic application concerns.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

MATCHING_INPUT_VERSION = "paper_page_matching_input.v1"
MATCHING_OUTPUT_VERSION = "paper_page_matching_output.v1"
PAPER_CHECK_INPUT_VERSION = "paper_check_input.v1"
PAPER_CHECK_OUTPUT_VERSION = "paper_check_output.v1"
NORMALIZED_UPRIGHT_V1 = "normalized_upright_v1"
MAX_PAGES = 512
MAX_ROSTER = 512
MAX_ITEMS = 256
MAX_FINDINGS = 64
MAX_CANDIDATES = 8
MAX_TEXT = 4_000
Token = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]
Confidence = Annotated[Decimal, Field(strict=True, ge=Decimal("0"), le=Decimal("1"))]
UnitCoordinate = Annotated[
    Decimal, Field(strict=True, ge=Decimal("0"), le=Decimal("1"))
]


class _Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class AssessmentScanBatchState(StrEnum):
    DRAFT = "draft"
    UPLOADING = "uploading"
    EXTRACTING = "extracting"
    MATCHING = "matching"
    MATCHING_REVIEW_REQUIRED = "matching_review_required"
    GROUPING_CONFIRMED = "grouping_confirmed"
    READY_FOR_CHECKING = "ready_for_checking"
    CHECKING = "checking"
    CHECKING_COMPLETED = "checking_completed"
    COMPLETED = "completed"
    PROCESSING_FAILED = "processing_failed"
    CANCELLED = "cancelled"


_TRANSITIONS = {
    AssessmentScanBatchState.DRAFT: {
        AssessmentScanBatchState.UPLOADING,
        AssessmentScanBatchState.CANCELLED,
    },
    AssessmentScanBatchState.UPLOADING: {
        AssessmentScanBatchState.EXTRACTING,
        AssessmentScanBatchState.PROCESSING_FAILED,
        AssessmentScanBatchState.CANCELLED,
    },
    AssessmentScanBatchState.EXTRACTING: {
        AssessmentScanBatchState.MATCHING,
        AssessmentScanBatchState.PROCESSING_FAILED,
        AssessmentScanBatchState.CANCELLED,
    },
    AssessmentScanBatchState.MATCHING: {
        AssessmentScanBatchState.MATCHING_REVIEW_REQUIRED,
        AssessmentScanBatchState.PROCESSING_FAILED,
        AssessmentScanBatchState.CANCELLED,
    },
    AssessmentScanBatchState.MATCHING_REVIEW_REQUIRED: {
        AssessmentScanBatchState.GROUPING_CONFIRMED,
        AssessmentScanBatchState.CANCELLED,
    },
    AssessmentScanBatchState.GROUPING_CONFIRMED: {
        AssessmentScanBatchState.READY_FOR_CHECKING,
        AssessmentScanBatchState.CANCELLED,
    },
    AssessmentScanBatchState.READY_FOR_CHECKING: {
        AssessmentScanBatchState.CHECKING,
        AssessmentScanBatchState.CANCELLED,
    },
    AssessmentScanBatchState.CHECKING: {
        AssessmentScanBatchState.CHECKING_COMPLETED,
        AssessmentScanBatchState.PROCESSING_FAILED,
        AssessmentScanBatchState.CANCELLED,
    },
    AssessmentScanBatchState.CHECKING_COMPLETED: {
        AssessmentScanBatchState.COMPLETED,
        AssessmentScanBatchState.CANCELLED,
    },
    AssessmentScanBatchState.PROCESSING_FAILED: {AssessmentScanBatchState.CANCELLED},
    AssessmentScanBatchState.COMPLETED: set(),
    AssessmentScanBatchState.CANCELLED: set(),
}


def validate_batch_transition(
    current: AssessmentScanBatchState, target: AssessmentScanBatchState
) -> None:
    """Reject skips; confirmation is deliberately a visible mandatory state."""
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"invalid_batch_transition:{current.value}:{target.value}")


class NormalizedPoint(_Contract):
    kind: Literal["point"] = "point"
    coordinate_space: Literal["normalized_upright_v1"] = NORMALIZED_UPRIGHT_V1
    x: UnitCoordinate
    y: UnitCoordinate


class NormalizedBox(_Contract):
    kind: Literal["box"] = "box"
    coordinate_space: Literal["normalized_upright_v1"] = NORMALIZED_UPRIGHT_V1
    x: UnitCoordinate
    y: UnitCoordinate
    width: UnitCoordinate
    height: UnitCoordinate

    @model_validator(mode="after")
    def fits_page(self) -> "NormalizedBox":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("region_outside_page")
        return self


SemanticRegion = Annotated[NormalizedPoint | NormalizedBox, Field(discriminator="kind")]


class ProviderContentDescriptor(_Contract):
    """Opaque provider-staged content; never a persistence/storage reference."""

    content_token: Token
    mime_type: StrictStr = Field(min_length=3, max_length=127)
    content_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class MatchingAssessmentContext(_Contract):
    title: StrictStr = Field(min_length=1, max_length=500)
    hints: tuple[StrictStr, ...] = Field(default=(), max_length=16)


class MatchingRosterEntry(_Contract):
    roster_token: Token
    display_name: StrictStr = Field(min_length=1, max_length=300)
    aliases: tuple[StrictStr, ...] = Field(default=(), max_length=8)


class MatchingPage(_Contract):
    page_token: Token
    source_order: StrictInt = Field(ge=0)
    content: ProviderContentDescriptor


class PageMatchingRequest(_Contract):
    schema_version: Literal["paper_page_matching_input.v1"] = MATCHING_INPUT_VERSION
    batch_token: Token
    assessment: MatchingAssessmentContext
    roster: tuple[MatchingRosterEntry, ...] = Field(min_length=1, max_length=MAX_ROSTER)
    pages: tuple[MatchingPage, ...] = Field(min_length=1, max_length=MAX_PAGES)

    @model_validator(mode="after")
    def unique_tokens(self) -> "PageMatchingRequest":
        if len({entry.roster_token for entry in self.roster}) != len(self.roster):
            raise ValueError("duplicate_roster_token")
        if len({page.page_token for page in self.pages}) != len(self.pages):
            raise ValueError("duplicate_page_token")
        if len({page.source_order for page in self.pages}) != len(self.pages):
            raise ValueError("duplicate_source_order")
        return self


class MatchDisposition(StrEnum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"


class PageMatchResult(_Contract):
    page_token: Token
    disposition: Literal["matched", "ambiguous", "unmatched"]
    proposed_roster_token: Token | None = None
    candidate_roster_tokens: tuple[Token, ...] = Field(
        default=(), max_length=MAX_CANDIDATES
    )
    confidence: Confidence
    evidence_code: Token
    evidence_summary: StrictStr = Field(min_length=1, max_length=MAX_TEXT)
    proposed_group_token: Token | None = None
    proposed_page_order: StrictInt | None = Field(default=None, ge=0)
    requires_human_review: StrictBool

    @model_validator(mode="after")
    def disposition_is_consistent(self) -> "PageMatchResult":
        if len(set(self.candidate_roster_tokens)) != len(self.candidate_roster_tokens):
            raise ValueError("duplicate_candidate")
        if (
            self.disposition == MatchDisposition.MATCHED
            and self.proposed_roster_token is None
        ):
            raise ValueError("matched_requires_roster")
        if self.disposition == MatchDisposition.UNMATCHED and (
            self.proposed_roster_token is not None or self.candidate_roster_tokens
        ):
            raise ValueError("unmatched_forbids_roster")
        if (
            self.disposition == MatchDisposition.AMBIGUOUS
            and self.proposed_roster_token is not None
        ):
            raise ValueError("ambiguous_forbids_selected_roster")
        return self


class PageMatchingResponse(_Contract):
    schema_version: Literal["paper_page_matching_output.v1"] = MATCHING_OUTPUT_VERSION
    pages: tuple[PageMatchResult, ...] = Field(min_length=1, max_length=MAX_PAGES)

    @model_validator(mode="after")
    def unique_pages(self) -> "PageMatchingResponse":
        if len({page.page_token for page in self.pages}) != len(self.pages):
            raise ValueError("duplicate_page_result")
        return self


def validate_matching_response(
    request: PageMatchingRequest, response: PageMatchingResponse
) -> None:
    expected_pages = {page.page_token for page in request.pages}
    actual_pages = {page.page_token for page in response.pages}
    if actual_pages != expected_pages:
        raise ValueError("page_coverage_mismatch")
    roster = {entry.roster_token for entry in request.roster}
    claimed = {
        token
        for result in response.pages
        for token in ((result.proposed_roster_token,) + result.candidate_roster_tokens)
        if token is not None
    }
    if not claimed <= roster:
        raise ValueError("unknown_roster_token")


class ConfirmedPaperGroup(_Contract):
    assignment_participant_id: UUID
    ordered_scan_page_ids: tuple[UUID, ...] = Field(min_length=1, max_length=MAX_PAGES)

    @model_validator(mode="after")
    def unique_pages(self) -> "ConfirmedPaperGroup":
        if len(set(self.ordered_scan_page_ids)) != len(self.ordered_scan_page_ids):
            raise ValueError("duplicate_page_order")
        return self


class PageGroupingRevision(_Contract):
    expected_revision: StrictInt = Field(ge=0)
    groups: tuple[ConfirmedPaperGroup, ...] = Field(max_length=MAX_ROSTER)
    unmatched_scan_page_ids: tuple[UUID, ...] = Field(max_length=MAX_PAGES)


def validate_grouping_revision(
    revision: PageGroupingRevision,
    *,
    known_page_ids: set[UUID],
    assignment_participant_ids: set[UUID],
    confirm: bool,
) -> None:
    participants = [group.assignment_participant_id for group in revision.groups]
    if len(set(participants)) != len(participants):
        raise ValueError("duplicate_participant")
    if not set(participants) <= assignment_participant_ids:
        raise ValueError("participant_not_in_assignment")
    assigned = [
        page for group in revision.groups for page in group.ordered_scan_page_ids
    ]
    unmatched = list(revision.unmatched_scan_page_ids)
    all_declared = assigned + unmatched
    if len(set(all_declared)) != len(all_declared):
        raise ValueError("duplicate_page_assignment")
    if set(all_declared) != known_page_ids:
        raise ValueError("page_coverage_mismatch")
    if confirm and unmatched:
        raise ValueError("unmatched_pages_block_confirmation")


class PaperCheckPage(_Contract):
    page_token: Token
    order: StrictInt = Field(ge=0)
    width: StrictInt = Field(gt=0, le=100_000)
    height: StrictInt = Field(gt=0, le=100_000)
    coordinate_space: Literal["normalized_upright_v1"] = NORMALIZED_UPRIGHT_V1
    content: ProviderContentDescriptor


class PaperCheckAssessmentItem(_Contract):
    item_token: Token
    position: StrictInt = Field(ge=0)
    max_score: Annotated[Decimal, Field(strict=True, gt=Decimal("0"))]
    task_context: StrictStr = Field(min_length=1, max_length=30_000)
    rubric_context: StrictStr = Field(min_length=1, max_length=30_000)


class PaperCheckGradingContext(_Contract):
    policy_version: StrictStr = Field(min_length=1, max_length=128)
    policy_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class PromptContractMetadata(_Contract):
    version: StrictStr = Field(min_length=1, max_length=128)
    fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class PaperCheckRequest(_Contract):
    schema_version: Literal["paper_check_input.v1"] = PAPER_CHECK_INPUT_VERSION
    paper_work_token: Token
    pages: tuple[PaperCheckPage, ...] = Field(min_length=1, max_length=MAX_PAGES)
    assessment_items: tuple[PaperCheckAssessmentItem, ...] = Field(
        min_length=1, max_length=MAX_ITEMS
    )
    grading_policy: PaperCheckGradingContext
    prompt_contract: PromptContractMetadata

    @model_validator(mode="after")
    def tokens_and_orders_are_unique(self) -> "PaperCheckRequest":
        for values, error in (
            ([p.page_token for p in self.pages], "duplicate_page_token"),
            ([p.order for p in self.pages], "duplicate_page_order"),
            ([i.item_token for i in self.assessment_items], "duplicate_item_token"),
            ([i.position for i in self.assessment_items], "duplicate_item_position"),
        ):
            if len(set(values)) != len(values):
                raise ValueError(error)
        return self


class PaperItemStatus(StrEnum):
    CORRECT = "correct"
    PARTIALLY_CORRECT = "partially_correct"
    INCORRECT = "incorrect"
    NOT_ATTEMPTED = "not_attempted"
    UNREADABLE = "unreadable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class PaperFinding(_Contract):
    finding_token: Token
    category: Token
    short_explanation: StrictStr = Field(min_length=1, max_length=500)
    detailed_explanation: StrictStr | None = Field(
        default=None, min_length=1, max_length=MAX_TEXT
    )
    severity: Literal["info", "warning", "error"]
    page_token: Token
    region: SemanticRegion
    confidence: Confidence
    requires_human_review: StrictBool


class PaperItemResult(_Contract):
    item_token: Token
    status: Literal[
        "correct",
        "partially_correct",
        "incorrect",
        "not_attempted",
        "unreadable",
        "insufficient_evidence",
    ]
    rubric_score: Annotated[Decimal, Field(strict=True, ge=Decimal("0"))] | None
    rubric_max_score: Annotated[Decimal, Field(strict=True, gt=Decimal("0"))]
    confidence: Confidence
    requires_human_review: StrictBool
    findings: tuple[PaperFinding, ...] = Field(default=(), max_length=MAX_FINDINGS)

    @model_validator(mode="after")
    def score_is_bounded(self) -> "PaperItemResult":
        if self.rubric_score is not None and self.rubric_score > self.rubric_max_score:
            raise ValueError("rubric_score_out_of_range")
        if (
            self.status
            in {
                PaperItemStatus.NOT_ATTEMPTED,
                PaperItemStatus.UNREADABLE,
                PaperItemStatus.INSUFFICIENT_EVIDENCE,
            }
            and self.rubric_score is not None
        ):
            raise ValueError("indeterminate_status_forbids_score")
        return self


class PaperCheckResponse(_Contract):
    schema_version: Literal["paper_check_output.v1"] = PAPER_CHECK_OUTPUT_VERSION
    items: tuple[PaperItemResult, ...] = Field(min_length=1, max_length=MAX_ITEMS)
    summary_draft: StrictStr = Field(min_length=1, max_length=MAX_TEXT)
    overall_confidence: Confidence
    requires_human_review: StrictBool

    @model_validator(mode="after")
    def unique_items(self) -> "PaperCheckResponse":
        if len({item.item_token for item in self.items}) != len(self.items):
            raise ValueError("duplicate_item_result")
        return self


def validate_checking_response(
    request: PaperCheckRequest, response: PaperCheckResponse
) -> None:
    expected = {item.item_token: item.max_score for item in request.assessment_items}
    returned = {item.item_token for item in response.items}
    if returned != set(expected):
        raise ValueError("item_coverage_mismatch")
    pages = {page.page_token for page in request.pages}
    for item in response.items:
        if item.rubric_max_score != expected[item.item_token]:
            raise ValueError("rubric_max_score_mismatch")
        if any(finding.page_token not in pages for finding in item.findings):
            raise ValueError("unknown_page_token")
