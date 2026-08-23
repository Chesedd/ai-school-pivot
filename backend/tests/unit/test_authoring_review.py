from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application.authoring_review import AuthoringReviewDraftV1, review_dto, revision_diff


DRAFT = {"schema_version":"authoring_review_draft.v1","title":"Draft","statement":"2 + 2?",
    "task_type":"calculation","answer_format":"number","choice_options":[],"expected_answer":"4",
    "solution":"Add two and two.","hints":["Count."]}


def test_review_draft_is_bounded_immutable_and_excludes_catalog_and_metadata():
    draft = AuthoringReviewDraftV1.model_validate_json(__import__("json").dumps(DRAFT))
    assert draft.statement == "2 + 2?"
    with pytest.raises(ValidationError):
        draft.statement = "Changed"
    for protected in ("subject", "grade", "skills", "provider_id", "validation_result"):
        with pytest.raises(ValidationError):
            AuthoringReviewDraftV1.model_validate({**DRAFT, protected:"forbidden"})
    with pytest.raises(ValidationError):
        AuthoringReviewDraftV1.model_validate({**DRAFT, "statement":"x" * 30001})


def test_review_edit_validation_preserves_choice_invariants():
    choice = {**DRAFT,"task_type":"test","answer_format":"single_choice",
        "choice_options":[{"key":"a","content":"Three"},{"key":"b","content":"Four"}],
        "expected_answer":"missing"}
    with pytest.raises(ValidationError): AuthoringReviewDraftV1.model_validate(choice)
    choice["expected_answer"] = "b"
    assert AuthoringReviewDraftV1.model_validate_json(__import__("json").dumps(choice)).expected_answer == "b"


def test_review_response_exposes_state_and_optimistic_version_only():
    review = SimpleNamespace(session_id=uuid4(),state="reviewing",version=3,draft=DRAFT,
        created_at=None,updated_at=None)
    response = review_dto(review)
    assert response["state"] == "reviewing" and response["version"] == 3
    assert "generated_draft" not in response and "frozen_request" not in response


def test_revision_diff_is_deterministic_and_returns_changed_fields_only():
    current = {**DRAFT, "title":"Human title", "choice_options":[{"key":"a","content":"Four"}],
        "hints":["Add."]}
    assert revision_diff(DRAFT, current) == [
        {"field":"title","from":"Draft","to":"Human title"},
        {"field":"choices","from":[],"to":[{"key":"a","content":"Four"}]},
        {"field":"hints","from":["Count."],"to":["Add."]},
    ]


def test_revision_diff_reports_no_unchanged_fields():
    assert revision_diff(DRAFT, dict(DRAFT)) == []
