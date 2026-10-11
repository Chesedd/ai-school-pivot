"""Pure source contracts, frozen score eligibility and publication membership."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.application.paper_remediation import (
    published_signals,
    source_type,
    validate_signals,
)
from app.application.remediation import RemediationError
from app.presentation.remediation_schemas import (
    CreatePaperRemediation,
    CreateRemediation,
)
from pydantic import ValidationError
from tests.unit.test_paper_result_publication import approved, snapshot


@pytest.mark.parametrize(
    "digital,paper,publication,expected",
    [
        (True, False, False, "digital"),
        (False, True, True, "paper"),
        (False, False, False, None),
        (True, True, True, None),
        (False, True, False, None),
        (False, False, True, None),
        (True, False, True, None),
        (True, True, False, None),
    ],
)
def test_exact_source_xor(digital, paper, publication, expected):
    values = [uuid4() if x else None for x in (digital, paper, publication)]
    if expected:
        assert source_type(*values) == expected
    else:
        with pytest.raises(RemediationError, match="source_invalid"):
            source_type(*values)


def stored():
    value = snapshot(approved())
    return SimpleNamespace(
        publication_snapshot=value.model_dump(mode="json"),
        approved_review_revision_id=value.approved_review_revision_id,
    ), value


@pytest.mark.parametrize(
    "status,score,expected",
    [
        ("correct", "10", False),
        ("partially_correct", "5", True),
        ("incorrect", "0", True),
        ("not_attempted", "0", True),
    ],
)
def test_score_eligibility_ignores_final_grade(status, score, expected):
    row, value = stored()
    # Keep the fixture's other items valid, with one selected status under test.
    data = row.publication_snapshot
    for item in data["items"]:
        item.update(status="correct", accepted_score=item["max_score"])
    item = data["items"][0]
    from decimal import Decimal

    maximum = Decimal(item["max_score"])
    accepted = (
        maximum
        if status == "correct"
        else maximum / 2
        if status == "partially_correct"
        else Decimal(0)
    )
    item.update(status=status, accepted_score=str(accepted))
    data["grade"]["calculated_total_score"] = str(
        sum(Decimal(x["accepted_score"]) for x in data["items"])
    )
    data["grade"]["final_grade"] = "Teacher override"
    _, eligible = published_signals(row, value.paper_submission_id)
    assert bool(eligible) is expected


@pytest.mark.parametrize(
    "corruption",
    [
        "foreign_paper",
        "foreign_revision",
        "duplicate_item",
        "duplicate_finding",
        "unreadable",
        "overflow",
        "inconsistent_status",
        "invalid_total",
    ],
)
def test_corrupt_snapshot_fails_closed(corruption):
    row, value = stored()
    data = row.publication_snapshot
    if corruption == "foreign_paper":
        data["paper_submission_id"] = str(uuid4())
    if corruption == "foreign_revision":
        data["approved_review_revision_id"] = str(uuid4())
    if corruption == "duplicate_item":
        data["items"].append(data["items"][0])
    if corruption == "duplicate_finding":
        data["findings"].append(data["findings"][0])
    if corruption == "unreadable":
        data["items"][0]["status"] = "unreadable"
    if corruption == "overflow":
        data["items"][0]["accepted_score"] = "999999"
    if corruption == "inconsistent_status":
        data["items"][0].update(status="correct", accepted_score="0")
    if corruption == "invalid_total":
        data["grade"]["calculated_total_score"] = "999999"
    with pytest.raises(RemediationError, match="publication_invalid"):
        published_signals(row, value.paper_submission_id)


def test_signal_membership_and_no_invented_taxonomy():
    row, value = stored()
    from decimal import Decimal

    data = row.publication_snapshot
    data["items"][0].update(
        status="partially_correct",
        accepted_score=str(Decimal(data["items"][0]["max_score"]) / 2),
    )
    data["grade"]["calculated_total_score"] = str(
        sum(Decimal(x["accepted_score"]) for x in data["items"])
    )
    snapshot_value, eligible = published_signals(row, value.paper_submission_id)
    assert eligible
    item = next(iter(eligible))
    finding = snapshot_value.findings[0].finding_id
    validate_signals(snapshot_value, eligible, [item], [finding])
    full_credit = next(
        x.assessment_item_id
        for x in snapshot_value.items
        if x.accepted_score == x.max_score
    )
    with pytest.raises(RemediationError, match="item_ineligible"):
        validate_signals(snapshot_value, eligible, [full_credit], [])
    for items, findings in [
        ([], []),
        ([uuid4()], []),
        ([item], [uuid4()]),
        ([item, item], []),
    ]:
        with pytest.raises(RemediationError):
            validate_signals(snapshot_value, eligible, items, findings)
    assert "source_check_finding_id" not in snapshot_value.findings[0].model_dump()
    assert "skill_id" not in snapshot_value.findings[0].model_dump()


def test_separate_strict_requests_preserve_digital_required_fields():
    with pytest.raises(ValidationError):
        CreateRemediation(creation_key="key", title="Title", source_submission_id=None)
    with pytest.raises(ValidationError):
        CreatePaperRemediation(
            creation_key="key",
            title="Title",
            source_paper_publication_id=uuid4(),
            source_assessment_item_ids=[uuid4()],
            student_id=uuid4(),
        )
