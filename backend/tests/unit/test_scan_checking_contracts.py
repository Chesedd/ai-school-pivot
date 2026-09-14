from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application.scan_checking_contracts import (
    MATCHING_INPUT_VERSION,
    PAPER_CHECK_INPUT_VERSION,
    AssessmentScanBatchState,
    ConfirmedPaperGroup,
    MatchingAssessmentContext,
    MatchingPage,
    MatchingRosterEntry,
    NormalizedBox,
    NormalizedPoint,
    PageGroupingRevision,
    PageMatchResult,
    PageMatchingRequest,
    PageMatchingResponse,
    PaperCheckAssessmentItem,
    PaperCheckGradingContext,
    PaperCheckPage,
    PaperCheckRequest,
    PaperCheckResponse,
    PaperFinding,
    PaperItemResult,
    PromptContractMetadata,
    ProviderContentDescriptor,
    validate_batch_transition,
    validate_checking_response,
    validate_grouping_revision,
    validate_matching_response,
)

D = Decimal
HASH = "a" * 64


def content(token="content-1"):
    return ProviderContentDescriptor(
        content_token=token, mime_type="image/png", content_sha256=HASH
    )


def matching_request():
    return PageMatchingRequest(
        batch_token="batch-1",
        assessment=MatchingAssessmentContext(title="Алгебра"),
        roster=(
            MatchingRosterEntry(roster_token="roster-1", display_name="Анна Иванова"),
        ),
        pages=(MatchingPage(page_token="page-1", source_order=0, content=content()),),
    )


def match(**changes):
    data = dict(
        page_token="page-1",
        disposition="matched",
        proposed_roster_token="roster-1",
        confidence=D(".9"),
        evidence_code="name",
        evidence_summary="Name is legible.",
        proposed_group_token="group-1",
        proposed_page_order=0,
        requires_human_review=False,
    )
    data.update(changes)
    return PageMatchResult.model_validate(data)


def checking_request():
    return PaperCheckRequest(
        paper_work_token="paper-1",
        pages=(
            PaperCheckPage(
                page_token="page-1", order=0, width=1000, height=1400, content=content()
            ),
        ),
        assessment_items=(
            PaperCheckAssessmentItem(
                item_token="item-1",
                position=0,
                max_score=D("2.5"),
                task_context="Solve.",
                rubric_context="Two and a half points.",
            ),
        ),
        grading_policy=PaperCheckGradingContext(
            policy_version="v1", policy_fingerprint=HASH
        ),
        prompt_contract=PromptContractMetadata(version="v1", fingerprint=HASH),
    )


def item_result(**changes):
    data = dict(
        item_token="item-1",
        status="correct",
        rubric_score=D("2.5"),
        rubric_max_score=D("2.5"),
        confidence=D(".9"),
        requires_human_review=False,
        findings=(),
    )
    data.update(changes)
    return PaperItemResult.model_validate(data)


def response(*items, **changes):
    data = dict(
        items=items or (item_result(),),
        summary_draft="Complete.",
        overall_confidence=D(".9"),
        requires_human_review=False,
    )
    data.update(changes)
    return PaperCheckResponse.model_validate(data)


def test_batch_state_machine_allows_only_staged_flow_and_cancellation():
    path = [
        AssessmentScanBatchState.DRAFT,
        AssessmentScanBatchState.UPLOADING,
        AssessmentScanBatchState.EXTRACTING,
        AssessmentScanBatchState.MATCHING,
        AssessmentScanBatchState.MATCHING_REVIEW_REQUIRED,
        AssessmentScanBatchState.GROUPING_CONFIRMED,
        AssessmentScanBatchState.READY_FOR_CHECKING,
        AssessmentScanBatchState.CHECKING,
        AssessmentScanBatchState.CHECKING_COMPLETED,
        AssessmentScanBatchState.COMPLETED,
    ]
    for current, target in zip(path, path[1:]):
        validate_batch_transition(current, target)
    with pytest.raises(ValueError):
        validate_batch_transition(
            AssessmentScanBatchState.EXTRACTING, AssessmentScanBatchState.CHECKING
        )
    with pytest.raises(ValueError):
        validate_batch_transition(
            AssessmentScanBatchState.MATCHING_REVIEW_REQUIRED,
            AssessmentScanBatchState.READY_FOR_CHECKING,
        )
    with pytest.raises(ValueError):
        validate_batch_transition(
            AssessmentScanBatchState.CANCELLED, AssessmentScanBatchState.DRAFT
        )


@pytest.mark.parametrize(
    "contract",
    [
        lambda: NormalizedPoint(x=D("0"), y=D("1")),
        lambda: NormalizedBox(x=D(".25"), y=D(".5"), width=D(".75"), height=D(".5")),
    ],
)
def test_normalized_regions_accept_exact_boundaries(contract):
    assert contract().coordinate_space == "normalized_upright_v1"


@pytest.mark.parametrize(
    "values",
    [
        {"x": D("-.01"), "y": D("0")},
        {"x": D("1.01"), "y": D("0")},
    ],
)
def test_point_rejects_coordinates_outside_unit_interval(values):
    with pytest.raises(ValidationError):
        NormalizedPoint(**values)


def test_box_rejects_overflow():
    with pytest.raises(ValidationError):
        NormalizedBox(x=D(".8"), y=D("0"), width=D(".3"), height=D("1"))


def test_matching_disposition_rules_are_closed():
    with pytest.raises(ValidationError):
        match(proposed_roster_token=None)
    with pytest.raises(ValidationError):
        match(disposition="unmatched")
    with pytest.raises(ValidationError):
        match(disposition="ambiguous")
    assert (
        match(
            disposition="unmatched",
            proposed_roster_token=None,
            proposed_group_token=None,
        ).disposition
        == "unmatched"
    )


def test_matching_request_bound_tokens_and_complete_unique_page_coverage():
    request = matching_request()
    validate_matching_response(request, PageMatchingResponse(pages=(match(),)))
    with pytest.raises(ValueError):
        validate_matching_response(
            request, PageMatchingResponse(pages=(match(page_token="unknown"),))
        )
    with pytest.raises(ValueError):
        validate_matching_response(
            request,
            PageMatchingResponse(pages=(match(proposed_roster_token="unknown"),)),
        )
    with pytest.raises(ValidationError):
        PageMatchingResponse(pages=(match(), match()))


def test_grouping_validates_uniqueness_coverage_membership_and_confirmation():
    participant, other, page1, page2 = uuid4(), uuid4(), uuid4(), uuid4()
    good = PageGroupingRevision(
        expected_revision=1,
        groups=(
            ConfirmedPaperGroup(
                assignment_participant_id=participant,
                ordered_scan_page_ids=(page1, page2),
            ),
        ),
        unmatched_scan_page_ids=(),
    )
    validate_grouping_revision(
        good,
        known_page_ids={page1, page2},
        assignment_participant_ids={participant},
        confirm=True,
    )
    with pytest.raises(ValidationError):
        ConfirmedPaperGroup(
            assignment_participant_id=participant, ordered_scan_page_ids=(page1, page1)
        )
    duplicate_participant = PageGroupingRevision(
        expected_revision=1,
        groups=(
            ConfirmedPaperGroup(
                assignment_participant_id=participant, ordered_scan_page_ids=(page1,)
            ),
            ConfirmedPaperGroup(
                assignment_participant_id=participant, ordered_scan_page_ids=(page2,)
            ),
        ),
        unmatched_scan_page_ids=(),
    )
    with pytest.raises(ValueError):
        validate_grouping_revision(
            duplicate_participant,
            known_page_ids={page1, page2},
            assignment_participant_ids={participant},
            confirm=True,
        )
    duplicate_page = PageGroupingRevision(
        expected_revision=1,
        groups=(
            ConfirmedPaperGroup(
                assignment_participant_id=participant, ordered_scan_page_ids=(page1,)
            ),
        ),
        unmatched_scan_page_ids=(page1,),
    )
    with pytest.raises(ValueError):
        validate_grouping_revision(
            duplicate_page,
            known_page_ids={page1},
            assignment_participant_ids={participant},
            confirm=False,
        )
    with pytest.raises(ValueError):
        validate_grouping_revision(
            good,
            known_page_ids={page1, page2, uuid4()},
            assignment_participant_ids={participant},
            confirm=True,
        )
    unmatched = PageGroupingRevision(
        expected_revision=1,
        groups=(
            ConfirmedPaperGroup(
                assignment_participant_id=participant, ordered_scan_page_ids=(page1,)
            ),
        ),
        unmatched_scan_page_ids=(page2,),
    )
    with pytest.raises(ValueError):
        validate_grouping_revision(
            unmatched,
            known_page_ids={page1, page2},
            assignment_participant_ids={participant},
            confirm=True,
        )
    with pytest.raises(ValueError):
        validate_grouping_revision(
            good,
            known_page_ids={page1, page2},
            assignment_participant_ids={other},
            confirm=True,
        )


def test_check_output_validates_request_tokens_scores_regions_and_coverage():
    request = checking_request()
    validate_checking_response(request, response())
    with pytest.raises(ValueError):
        validate_checking_response(request, response(item_result(item_token="unknown")))
    with pytest.raises(ValidationError):
        response(item_result(), item_result())
    with pytest.raises(ValidationError):
        item_result(rubric_score=D("2.51"))
    with pytest.raises(ValidationError):
        PaperFinding(
            finding_token="f-1",
            category="math",
            short_explanation="Error.",
            severity="error",
            page_token="page-1",
            region={
                "kind": "box",
                "x": D(".9"),
                "y": D("0"),
                "width": D(".2"),
                "height": D("1"),
            },
            confidence=D(".8"),
            requires_human_review=False,
        )
    finding = PaperFinding(
        finding_token="f-1",
        category="math",
        short_explanation="Error.",
        severity="error",
        page_token="unknown",
        region={"kind": "point", "x": D("0"), "y": D("0")},
        confidence=D(".8"),
        requires_human_review=False,
    )
    with pytest.raises(ValueError):
        validate_checking_response(request, response(item_result(findings=(finding,))))
    with pytest.raises(ValidationError):
        PaperCheckResponse.model_validate(
            {**response().model_dump(), "final_grade": "5"}
        )
    with pytest.raises(ValidationError):
        PaperCheckResponse.model_validate(
            {**response().model_dump(), "final_total_score": D("2.5")}
        )


def test_checking_privacy_and_matching_minimal_identity_are_schema_enforced():
    serialized = checking_request().model_dump(mode="json")
    forbidden = {
        "student_id",
        "participant_id",
        "user_id",
        "login",
        "email",
        "display_name",
        "storage_reference",
    }
    fields = set(PaperCheckRequest.model_json_schema()["properties"])
    assert forbidden.isdisjoint(fields)
    assert "Анна" not in str(serialized) and "storage_reference" not in str(serialized)
    roster_fields = set(MatchingRosterEntry.model_json_schema()["properties"])
    assert roster_fields == {"roster_token", "display_name", "aliases"}
    assert matching_request().schema_version == MATCHING_INPUT_VERSION
    assert checking_request().schema_version == PAPER_CHECK_INPUT_VERSION
    with pytest.raises(ValidationError):
        PaperCheckRequest.model_validate({**serialized, "student_id": str(uuid4())})
