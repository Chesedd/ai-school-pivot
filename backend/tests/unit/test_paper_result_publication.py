"""Frozen allowlist, deterministic grades and bounded publication commands."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application.paper_checking_review import (
    PaperReviewOverride,
    PaperReviewRevision,
    approve_scores,
)
from app.application.paper_result_publication import (
    PaperPublicationError,
    PublicationSnapshot,
    SelectedPublications,
    build_publication_snapshot,
)
from tests.unit.test_paper_checking_review import draft, finding
from tests.unit.test_scan_grading_policy import policy


def approved(**changes):
    value = draft(findings=(finding(), finding()))
    return PaperReviewRevision(
        id=uuid4(),
        review_id=uuid4(),
        paper_submission_id=value.paper_submission_id,
        check_run_id=value.check_run_id,
        ai_revision_id=value.ai_revision_id,
        revision_number=1,
        revision_kind="approved",
        snapshot=value,
        actor_user_id=uuid4(),
        grading_policy_revision=1,
        grading_policy_fingerprint=policy().fingerprint,
        change_summary=(),
        grade_result=approve_scores(policy(), value),
        approved_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    ).model_copy(update=changes)


def snapshot(revision):
    return build_publication_snapshot(
        revision, policy(), {f.scan_page_id for f in revision.snapshot.findings}
    )


def test_frozen_student_allowlist_and_exact_grade():
    revision = approved()
    value = snapshot(revision)
    assert (
        value.grade.calculated_total_score
        == revision.grade_result.calculated_total_score
    )
    assert value.grade.final_grade == revision.grade_result.final_grade
    assert PublicationSnapshot.model_validate_json(value.model_dump_json()) == value
    assert value.findings == tuple(
        sorted(value.findings, key=lambda f: str(f.finding_id))
    )
    for field in (
        "actor_user_id",
        "check_run_id",
        "ai_revision_id",
        "source_check_result_id",
        "source_check_finding_id",
        "storage_reference",
        "provider",
        "grade_override_reason",
    ):
        assert field not in value.model_dump_json()
    with pytest.raises(ValidationError):
        value.summary = "mutated"


@pytest.mark.parametrize("kind", ["ai_seed", "human_edit"])
def test_reject_nonapproved(kind):
    with pytest.raises(PaperPublicationError, match="revision_not_approved"):
        snapshot(approved(revision_kind=kind))


@pytest.mark.parametrize(
    "field", ["paper_submission_id", "check_run_id", "ai_revision_id"]
)
def test_fail_closed_provenance(field):
    with pytest.raises(PaperPublicationError, match="revision_mismatch"):
        snapshot(approved(**{field: uuid4()}))


@pytest.mark.parametrize("field", ["grade_result", "approved_at"])
def test_incomplete_approval(field):
    with pytest.raises(PaperPublicationError):
        snapshot(approved(**{field: None}))


def test_fail_closed_foreign_page():
    with pytest.raises(PaperPublicationError, match="foreign_page"):
        build_publication_snapshot(approved(), policy(), set())


def test_recompute_grade_and_hide_override_rationale():
    revision = approved()
    override = approve_scores(
        policy(),
        revision.snapshot,
        PaperReviewOverride(override_grade="teacher", reason="Internal rationale"),
    )
    value = snapshot(revision.model_copy(update={"grade_result": override}))
    assert value.grade.final_grade == "teacher"
    assert "Internal rationale" not in value.model_dump_json()
    with pytest.raises(PaperPublicationError, match="invalid_grade"):
        snapshot(
            revision.model_copy(
                update={
                    "grade_result": override.model_copy(
                        update={"calculated_grade": "wrong"}
                    )
                }
            )
        )


def test_teacher_added_corrected_and_removed_findings():
    revision = approved()
    removed = revision.snapshot.findings[0]
    kept = revision.snapshot.findings[1].model_copy(
        update={
            "short_explanation": "Teacher correction",
            "region": revision.snapshot.findings[1].region.model_copy(
                update={"x": Decimal("0.75")}
            ),
        }
    )
    added = finding(short_explanation="Teacher addition")
    revision = revision.model_copy(
        update={
            "snapshot": revision.snapshot.model_copy(update={"findings": (kept, added)})
        }
    )
    result = snapshot(revision)
    assert removed.finding_id not in {f.finding_id for f in result.findings}
    assert {f.short_explanation for f in result.findings} == {
        "Teacher correction",
        "Teacher addition",
    }
    assert next(
        f for f in result.findings if f.finding_id == kept.finding_id
    ).region.x == Decimal("0.75")


@pytest.mark.parametrize(
    "field", ["raw_output", "storage_reference", "source_pdf", "roster"]
)
def test_reject_unknown_snapshot_fields(field):
    data = snapshot(approved()).model_dump(mode="json")
    data[field] = "secret"
    with pytest.raises(ValidationError):
        PublicationSnapshot.model_validate_json(__import__("json").dumps(data))


def test_duplicate_selection_rejected():
    entry = {
        "paper_submission_id": str(uuid4()),
        "approved_review_revision_id": str(uuid4()),
    }
    with pytest.raises(ValidationError):
        SelectedPublications(entries=[entry, entry])
