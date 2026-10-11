"""Pure, deterministic batch projection and guarded aggregate transitions."""
from dataclasses import asdict
from uuid import uuid4

import pytest
from app.application.paper_batch_orchestration import CHECK_STATUSES, derive_progress
from app.application.scan_checking_contracts import AssessmentScanBatchState as State
from app.application.scan_checking_contracts import validate_batch_transition


def progress(statuses=(), *, state="checking", active=(), extra_runs=()):
    papers = tuple(uuid4() for _ in statuses)
    runs = tuple((p, 1, s) for p, s in zip(papers, statuses) if s is not None)
    runs += tuple((papers[i], n, s) for i, n, s in extra_runs)
    return derive_progress(uuid4(), state, papers, runs, {papers[i] for i in active})


def test_empty_cannot_settle_or_finalize():
    result = progress(state="checking_completed")
    assert result.paper_count == result.settled_count == 0
    assert not result.can_mark_checking_completed and not result.can_finalize


@pytest.mark.parametrize("status,settled", [
    (None, False), ("pending", False), ("running", False), ("failed_retryable", False),
    ("failed_terminal", True), ("completed", True), ("completed_with_review_required", True),
])
def test_checking_statuses(status, settled):
    result = progress((status,))
    assert result.can_mark_checking_completed is settled
    assert result.settled_count == int(settled)
    assert getattr(result, status or "not_started") == 1


@pytest.mark.parametrize("old,new,settled", [
    ("failed_retryable", "completed_with_review_required", True),
    ("completed_with_review_required", "failed_retryable", False),
    ("failed_terminal", "completed", True),
])
def test_latest_attempt_wins_regardless_of_input_order(old, new, settled):
    result = progress((old,), extra_runs=((0, 2, new), (0, 1, old)))
    assert result.can_mark_checking_completed is settled
    assert getattr(result, new) == 1
    assert getattr(result, old) == 0


@pytest.mark.parametrize("statuses", [
    ("completed", "completed_with_review_required"),
    ("completed_with_review_required", "failed_terminal"),
])
def test_all_settled(statuses):
    assert progress(statuses).can_mark_checking_completed


@pytest.mark.parametrize("active,allowed", [((), False), ((0,), False), ((0, 1), True)])
def test_only_all_active_pointers_allow_finalize(active, allowed):
    result = progress(("completed", "completed"), state="checking_completed", active=active)
    assert result.can_finalize is allowed
    assert result.active_publication_count == len(active)


def test_publication_does_not_automatically_finalize():
    assert not progress(("completed",), active=(0,)).can_finalize


def test_approval_and_history_are_not_inputs_to_projection():
    # Grade/Decimal data, approved revisions and historical publication rows
    # cannot contribute: the projection accepts only statuses and active IDs.
    assert not progress(("completed",), state="checking_completed").can_finalize


def test_foreign_paper_run_and_pointer_cannot_contribute():
    paper, foreign, batch = uuid4(), uuid4(), uuid4()
    result = derive_progress(batch, "checking", [paper], [(foreign, 99, "completed")], {foreign})
    assert result.not_started == 1 and result.active_publication_count == 0


def test_deterministic_counts_and_order():
    batch, a, b = uuid4(), uuid4(), uuid4()
    runs = [(a, 2, "completed"), (a, 1, "failed_retryable"), (b, 1, "failed_terminal")]
    first = derive_progress(batch, "checking", [a, b], runs, {a})
    second = derive_progress(batch, "checking", [b, a], reversed(runs), {a})
    assert first == second
    assert list(asdict(first))[3:3 + len(CHECK_STATUSES)] == list(CHECK_STATUSES)


def test_unknown_status_fails_closed():
    assert not progress(("future_status",)).can_mark_checking_completed


def test_reopen_requires_new_attempt_and_completed_never_reopens():
    with pytest.raises(ValueError):
        validate_batch_transition(State.CHECKING_COMPLETED, State.CHECKING)
    validate_batch_transition(State.CHECKING_COMPLETED, State.CHECKING, new_attempt=True)
    for state in State:
        with pytest.raises(ValueError):
            validate_batch_transition(State.COMPLETED, state, new_attempt=True)
