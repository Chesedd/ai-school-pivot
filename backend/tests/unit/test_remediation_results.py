from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.remediation_results import execution_state


NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize("submission,run,expected", [
    (None, None, "not_started"),
    (SimpleNamespace(status="draft"), None, "in_progress"),
    (SimpleNamespace(status="submitted"), None, "submitted"),
    (SimpleNamespace(status="submitted"), SimpleNamespace(status="pending"), "checking"),
    (SimpleNamespace(status="submitted"), SimpleNamespace(status="running"), "checking"),
    (SimpleNamespace(status="submitted"), SimpleNamespace(status="completed"), "checked"),
    (SimpleNamespace(status="submitted"), SimpleNamespace(status="completed_with_review_required"), "review_required"),
    (SimpleNamespace(status="submitted"), SimpleNamespace(status="failed_retryable"), "check_failed"),
    (SimpleNamespace(status="submitted"), SimpleNamespace(status="failed_terminal"), "check_failed"),
])
def test_execution_state_mapping(submission, run, expected):
    plan = SimpleNamespace(status="assigned", due_at=NOW + timedelta(days=1))
    assert execution_state(plan, submission, run, NOW) == expected


def test_expired_and_cancelled_availability():
    expired = SimpleNamespace(status="assigned", due_at=NOW - timedelta(seconds=1))
    cancelled = SimpleNamespace(status="cancelled", due_at=None)
    assert execution_state(expired, None, None, NOW) == "expired"
    assert execution_state(cancelled, None, None, NOW) == "cancelled"


def test_failed_latest_run_does_not_fall_back():
    plan = SimpleNamespace(status="assigned", due_at=None)
    submission = SimpleNamespace(status="submitted")
    runs = [SimpleNamespace(attempt_no=1, status="completed"),
            SimpleNamespace(attempt_no=2, status="failed_terminal")]
    latest = sorted(runs, key=lambda run: run.attempt_no, reverse=True)[0]
    assert execution_state(plan, submission, latest, NOW) == "check_failed"
