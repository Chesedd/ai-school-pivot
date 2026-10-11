"""Manual draft semantics reuse the deterministic canonical review rules."""

from decimal import Decimal
from uuid import uuid4

import pytest
from app.application.paper_batch_orchestration import derive_progress
from app.application.paper_checking_review import (
    PaperReviewDraft,
    PaperReviewError,
    ReviewSource,
    approve_scores,
)
from pydantic import ValidationError
from tests.unit.test_paper_checking_review import decision, finding
from tests.unit.test_scan_grading_policy import ITEM_1, ITEM_2, policy


def manual_draft(status=None, score=None, **changes):
    return PaperReviewDraft(
        paper_submission_id=uuid4(),
        source_kind="manual",
        check_run_id=None,
        ai_revision_id=None,
        summary="Teacher summary",
        items=tuple(
            decision(item, status, score, source_check_result_id=None)
            for item in (ITEM_1, ITEM_2)
        ),
        **changes,
    )


def test_manual_seed_is_ungraded_without_ai_lineage():
    draft = manual_draft()
    assert all(
        x.status is x.accepted_score is x.source_check_result_id is None
        for x in draft.items
    )
    with pytest.raises(PaperReviewError, match="unresolved"):
        approve_scores(policy(), draft)


@pytest.mark.parametrize(
    "status,score,valid",
    [
        (None, "0", False),
        ("correct", None, False),
        ("correct", "10", True),
        ("correct", "5", False),
        ("partially_correct", "5", True),
        ("partially_correct", "0", False),
        ("partially_correct", "10", False),
        ("incorrect", "0", True),
        ("incorrect", "1", False),
        ("not_attempted", "0", True),
        ("not_attempted", None, False),
        ("unreadable", "0", False),
        ("insufficient_evidence", "0", False),
    ],
)
def test_manual_approval_rules(status, score, valid):
    draft = manual_draft(status, score)
    if valid:
        assert (
            approve_scores(policy(), draft).calculated_total_score == Decimal(score) * 2
        )
    else:
        with pytest.raises(PaperReviewError):
            approve_scores(policy(), draft)


@pytest.mark.parametrize(
    "kind,run,ai,valid",
    [
        ("ai", True, True, True),
        ("manual", False, False, True),
        ("ai", False, True, False),
        ("ai", True, False, False),
        ("manual", True, False, False),
        ("manual", False, True, False),
    ],
)
def test_review_provenance_contract(kind, run, ai, valid):
    args = {
        "source_kind": kind,
        "check_run_id": uuid4() if run else None,
        "ai_revision_id": uuid4() if ai else None,
    }
    if valid:
        ReviewSource(**args)
    else:
        with pytest.raises(ValidationError):
            ReviewSource(**args)


def test_manual_finding_and_result_cannot_fabricate_ai_lineage():
    draft = manual_draft(findings=(finding(),))
    assert draft.findings[0].source_check_finding_id is None
    for changes in (
        {"findings": (finding(source_check_finding_id=uuid4()),)},
        {"items": (decision(), decision(ITEM_2))},
    ):
        with pytest.raises(ValidationError):
            PaperReviewDraft.model_validate({**draft.model_dump(), **changes})


@pytest.mark.parametrize(
    "failure,state,settled",
    [
        ("failed_retryable", "reviewing", False),
        ("failed_terminal", "reviewing", False),
        ("failed_retryable", "approved", True),
        ("failed_terminal", "approved", True),
    ],
)
def test_manual_state_takes_precedence_over_historical_failure(failure, state, settled):
    batch, paper = uuid4(), uuid4()
    progress = derive_progress(
        batch, "checking", [paper], [(paper, 1, failure)], set(), [(paper, state)]
    )
    assert progress.manual_reviewing == int(state == "reviewing")
    assert progress.manual_approved == int(state == "approved")
    assert progress.can_mark_checking_completed is settled
    assert progress.failed_retryable == progress.failed_terminal == 0


def test_empty_manual_summary_requires_teacher_completion():
    draft = manual_draft("correct", "10").model_copy(update={"summary": ""})
    with pytest.raises(PaperReviewError, match="summary_required"):
        approve_scores(policy(), draft)


@pytest.mark.parametrize(
    "status,score", [("correct", "10"), ("partially_correct", "5")]
)
def test_manual_approved_revision_enters_existing_publication(status, score):
    from datetime import UTC, datetime

    from app.application.paper_checking_review import PaperReviewRevision
    from app.application.paper_result_publication import build_publication_snapshot

    draft = manual_draft(status, score, findings=(finding(),))
    revision = PaperReviewRevision(
        id=uuid4(),
        review_id=uuid4(),
        paper_submission_id=draft.paper_submission_id,
        source_kind="manual",
        check_run_id=None,
        ai_revision_id=None,
        revision_number=2,
        revision_kind="approved",
        snapshot=draft,
        actor_user_id=uuid4(),
        grading_policy_revision=1,
        grading_policy_fingerprint=policy().fingerprint,
        change_summary=(),
        grade_result=approve_scores(policy(), draft),
        approved_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    published = build_publication_snapshot(
        revision, policy(), {x.scan_page_id for x in draft.findings}
    )
    assert published.items[0].accepted_score == Decimal(score)
    assert "source_kind" not in published.model_dump_json()
    assert "check_run" not in published.model_dump_json()
    assert published.grade.calculated_total_score == Decimal(score) * 2
