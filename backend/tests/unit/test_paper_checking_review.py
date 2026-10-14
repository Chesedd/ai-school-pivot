"""Contract, canonicalization and frozen-policy grading boundaries."""

from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from app.application.paper_checking_review import (
    PaperReviewApproval,
    PaperReviewDraft,
    PaperReviewError,
    PaperReviewFinding,
    PaperReviewItem,
    PaperReviewOverride,
    approve_scores,
    canonicalize_draft,
    changed_domains,
    seed_review_item,
)
from app.application.scan_grading_policy import (
    GradeScale,
    GradeThreshold,
    calculate_percentage,
)
from pydantic import ValidationError
from tests.unit.test_scan_grading_policy import ITEM_1, ITEM_2, policy

D = Decimal


def decision(identifier=ITEM_1, status="correct", score="10", **changes):
    return PaperReviewItem.model_validate(
        {
            "assessment_item_id": identifier,
            "source_check_result_id": uuid4(),
            "status": status,
            "accepted_score": None if score is None else D(score),
            "max_score": D(10),
            **changes,
        }
    )


def draft(**changes):
    return PaperReviewDraft.model_validate(
        {
            "paper_submission_id": uuid4(),
            "check_run_id": uuid4(),
            "ai_revision_id": uuid4(),
            "summary": "Teacher summary",
            "items": (decision(), decision(ITEM_2)),
            **changes,
        }
    )


def finding(**changes):
    return PaperReviewFinding.model_validate(
        {
            "finding_id": uuid4(),
            "assessment_item_id": ITEM_1,
            "scan_page_id": uuid4(),
            "category": "method",
            "severity": "warning",
            "short_explanation": "Check this step",
            "region": {"kind": "point", "x": D(".1"), "y": D(".2")},
            **changes,
        }
    )


@pytest.mark.parametrize(
    "status,score",
    [
        ("correct", "10"),
        ("partially_correct", ".01"),
        ("partially_correct", "9.99"),
        ("incorrect", "0"),
        ("not_attempted", "0"),
    ],
)
def test_resolved_scores(status, score):
    value = draft(items=(decision(status=status, score=score), decision(ITEM_2)))
    result = approve_scores(policy(), value)
    assert result.calculated_total_score == D(score) + D(10)
    assert result.total_max_score == D(20)
    assert result.final_grade == result.calculated_grade
    assert result.grade_override_reason is None


@pytest.mark.parametrize(
    "status,score",
    [
        ("correct", "9"),
        ("correct", None),
        ("partially_correct", "0"),
        ("partially_correct", "10"),
        ("partially_correct", None),
        ("incorrect", "1"),
        ("incorrect", None),
        ("not_attempted", "1"),
        ("not_attempted", None),
        ("unreadable", None),
        ("unreadable", "0"),
        ("insufficient_evidence", None),
        ("insufficient_evidence", "10"),
    ],
)
def test_unresolved_or_inconsistent_status_cannot_approve(status, score):
    with pytest.raises(PaperReviewError):
        approve_scores(
            policy(),
            draft(items=(decision(status=status, score=score), decision(ITEM_2))),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"accepted_score": 1.5},
        {"accepted_score": "1.5"},
        {"max_score": 10.0},
        {"accepted_score": D("NaN")},
        {"accepted_score": D("Infinity")},
        {"accepted_score": D(-1)},
        {"max_score": D(0)},
        {"accepted_score": D(11)},
        {"teacher_comment": "x" * 2001},
        {"extra": "forbidden"},
        {"status": "approved"},
    ],
)
def test_strict_bounded_item_contract(changes):
    with pytest.raises(ValidationError):
        decision(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"summary": "x" * 4001},
        {"summary": ""},
        {"schema_version": "v2"},
        {"storage_reference": "private"},
        {"calculated_grade": "A"},
    ],
)
def test_strict_draft_contract(changes):
    with pytest.raises(ValidationError):
        draft(**changes)


def test_duplicate_items_and_findings():
    item, f = decision(), finding()
    for changes in ({"items": (item, item)}, {"findings": (f, f)}):
        with pytest.raises(ValidationError):
            draft(**changes)
    with pytest.raises(ValidationError):
        item.status = "incorrect"


@pytest.mark.parametrize(
    "changes",
    [
        {"severity": "critical"},
        {"category": "x" * 129},
        {"short_explanation": "x" * 501},
        {"short_explanation": " "},
        {"detailed_explanation": "x" * 4001},
        {"coordinate_space_version": "pixels"},
        {"region": {"kind": "point", "x": 0.5, "y": D(0)}},
        {"region": {"kind": "point", "x": D("1.1"), "y": D(0)}},
        {
            "region": {
                "kind": "box",
                "x": D(".9"),
                "y": D(0),
                "width": D(".2"),
                "height": D(".1"),
            }
        },
    ],
)
def test_finding_geometry_and_text(changes):
    with pytest.raises(ValidationError):
        finding(**changes)


def test_canonical_order_and_technical_change_summary():
    a, b = decision(), decision(ITEM_2)
    f, g = finding(), finding()
    first = draft(items=(b, a), findings=(g, f))
    second = first.model_copy(update={"items": (a, b), "findings": (f, g)})
    first = canonicalize_draft(first, (ITEM_1, ITEM_2))
    second = canonicalize_draft(second, (ITEM_1, ITEM_2))
    assert first == second
    assert changed_domains(first, second) == ()
    assert changed_domains(first, first.model_copy(update={"summary": "New"})) == (
        "summary",
    )
    assert changed_domains(first, first.model_copy(update={"findings": ()})) == (
        "findings",
    )


@pytest.mark.parametrize("items", [(decision(),), (decision(), decision(uuid4()))])
def test_exact_item_coverage(items):
    with pytest.raises(PaperReviewError, match="coverage"):
        approve_scores(policy(), draft(items=items))


def test_maximum_tampering():
    with pytest.raises(PaperReviewError, match="policy_mismatch"):
        approve_scores(
            policy(), draft(items=(decision(max_score=D(11)), decision(ITEM_2)))
        )


def test_point_grade_and_override_preserve_calculated_grade():
    value = draft(
        items=(decision(status="partially_correct", score="4"), decision(ITEM_2))
    )
    result = approve_scores(
        policy(),
        value,
        PaperReviewOverride(
            override_grade="custom-label", reason="Explicit moderation"
        ),
    )
    assert result.calculated_grade == "4"
    assert result.final_grade == "custom-label"
    assert result.grade_override_reason == "Explicit moderation"


@pytest.mark.parametrize("reason", ["", " ", " untrimmed", "x" * 2001])
def test_override_requires_bounded_trimmed_reason(reason):
    with pytest.raises(ValidationError):
        PaperReviewOverride(override_grade="A", reason=reason)


def test_client_cannot_supply_calculated_grade():
    with pytest.raises(ValidationError):
        PaperReviewApproval(expected_version=1, calculated_grade="A")


def test_display_rounding_never_selects_percent_grade():
    frozen = policy(
        grade_scale=GradeScale(
            basis="percent",
            thresholds=(
                GradeThreshold(minimum=D(70), grade_label="pass"),
                GradeThreshold(minimum=D(0), grade_label="retry"),
            ),
        )
    )
    value = draft(
        items=(decision(status="partially_correct", score="3.999999"), decision(ITEM_2))
    )
    result = approve_scores(frozen, value)
    assert result.calculated_percentage == D("70.0000")
    assert result.calculated_grade == "retry"
    assert calculate_percentage(frozen, D("17.5")) == D("87.5000")
    assert calculate_percentage(frozen, D(".00001")) == D(".0001")


@pytest.mark.parametrize("total", [0.5, "1", D(-1), D(21), D("Infinity"), D("NaN")])
def test_percentage_rejects_non_decimal_and_out_of_range(total):
    with pytest.raises(ValueError):
        calculate_percentage(policy(), total)


def test_display_percentage_independent_of_ambient_decimal_precision():
    value = policy()
    with localcontext() as context:
        context.prec = 3
        assert calculate_percentage(value, D("13.99999999999")) == D("70.0000")


@pytest.mark.parametrize(
    "status,score,expected",
    [
        ("correct", D(10), D(10)),
        ("partially_correct", D(5), D(5)),
        ("incorrect", D(0), D(0)),
        ("not_attempted", None, None),
        ("unreadable", None, None),
        ("insufficient_evidence", None, None),
    ],
)
def test_seed_retains_result_lineage_and_never_fabricates_scores(
    status, score, expected
):
    source = uuid4()
    item = seed_review_item(
        assessment_item_id=ITEM_1,
        source_check_result_id=source,
        status=status,
        suggested_score=score,
        max_score=D(10),
    )
    assert item.accepted_score == expected and item.source_check_result_id == source
