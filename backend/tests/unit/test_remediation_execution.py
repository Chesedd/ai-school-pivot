"""Focused policy tests for the C9B remediation execution boundary."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.capabilities import (
    ROLE_CAPABILITIES, STUDENT_REMEDIATIONS_EXECUTE,
)
from app.application.remediation import RemediationError
from app.application.remediation_execution import RemediationExecutionService
from app.application.student_assessments import normalize_answer
from app.presentation.remediation_schemas import (
    RemediationAnswerPut, RemediationExecutionResponse,
)


def test_capability_is_student_and_admin_only():
    assert STUDENT_REMEDIATIONS_EXECUTE in ROLE_CAPABILITIES["student"]
    assert STUDENT_REMEDIATIONS_EXECUTE in ROLE_CAPABILITIES["admin"]
    assert STUDENT_REMEDIATIONS_EXECUTE not in ROLE_CAPABILITIES["teacher"]


def test_remediation_reuses_assessment_normalization():
    assert normalize_answer("number", " 01,500 ") == {"decimal": "1.5"}
    assert normalize_answer("multiple_choice", ["b", "a"]) == {"option_ids": ["a", "b"]}


def test_strict_request_rejects_actor_identity():
    with pytest.raises(ValueError):
        RemediationAnswerPut.model_validate({"raw_answer": "x", "student_id": "ignored"})


def test_execution_response_has_only_c9b_states():
    allowed = RemediationExecutionResponse.model_fields["execution_status"].annotation
    assert "checking" not in str(allowed)
    assert "submitted" in str(allowed)


def test_cancelled_mutation_is_rejected():
    with pytest.raises(RemediationError, match="remediation_cancelled"):
        RemediationExecutionService._mutable(
            SimpleNamespace(status="cancelled", due_at=None), datetime.now(timezone.utc))


def test_expired_mutation_is_rejected_but_null_deadline_is_allowed():
    now = datetime.now(timezone.utc)
    RemediationExecutionService._mutable(SimpleNamespace(status="assigned", due_at=None), now)
    with pytest.raises(RemediationError, match="remediation_expired"):
        RemediationExecutionService._mutable(
            SimpleNamespace(status="assigned", due_at=now - timedelta(seconds=1)), now)


def test_deadline_is_strictly_greater_than_due_at():
    now = datetime.now(timezone.utc)
    RemediationExecutionService._mutable(SimpleNamespace(status="assigned", due_at=now), now)
