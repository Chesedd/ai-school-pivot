from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application.authoring_api import AuthoringApiError
from app.application.authoring_promotion import ACCEPTABLE, QUESTIONABLE, PromoteAuthoringArtifactService
from app.presentation.authoring_schemas import AuthoringAcceptanceRequest


DRAFT = {"schema_version":"generated_task_draft.v1","title":"Draft","statement":"2 + 2?",
    "task_type":"calculation","answer_format":"number","choice_options":[],"expected_answer":"4",
    "solution":"Add two and two.","hints":["Count."]}
SOLVER = {"schema_version":"solver_result.v1","status":"solvable","proposed_answer":"4",
    "reasoning_summary":"Independent addition."}
VALIDATION = {"schema_version":"task_validation_result.v1","status":"validated","comparator":"decimal_v1"}


def row(**changes):
    values = dict(generated_draft=DRAFT,generator_attempt_id=uuid4(),solver_result=SOLVER,
        solver_attempt_id=uuid4(),validation_result=VALIDATION,semantic_status="validated")
    values.update(changes)
    return SimpleNamespace(**values)


def test_acceptance_policy_is_explicit_and_narrow():
    assert ACCEPTABLE == {"validated","answer_mismatch","manual_review_required"}
    assert QUESTIONABLE == {"answer_mismatch","manual_review_required"}
    artifact = PromoteAuthoringArtifactService(None)._artifact(row())
    assert artifact.generated_draft.statement == "2 + 2?"


def test_non_terminal_checkpoint_is_rejected():
    with pytest.raises(AuthoringApiError,match="authoring_artifact_incomplete"):
        PromoteAuthoringArtifactService(None)._artifact(row(solver_result=None,solver_attempt_id=None,validation_result=None))


def test_acceptance_request_cannot_override_content_or_catalog():
    request = AuthoringAcceptanceRequest.model_validate({"acceptance_note":"Reviewed", "confirm_questionable":True,
        "warning_override_reason":"Verified manually"})
    assert request.acceptance_note == "Reviewed"
    assert request.warning_override_reason == "Verified manually"
    assert AuthoringAcceptanceRequest.model_validate({"revision_number":0}).revision_number == 0
    with pytest.raises(ValidationError):
        AuthoringAcceptanceRequest.model_validate({"task_id":str(uuid4())})
    with pytest.raises(ValidationError):
        AuthoringAcceptanceRequest.model_validate({"acceptance_note":"x" * 2001})
