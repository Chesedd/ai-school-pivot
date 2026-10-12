"""Withdrawal command bounds and the versioned page grace policy."""

from uuid import uuid4

import pytest
from app.application.paper_result_publication import (
    SelectedWithdrawals,
    WithdrawalEntry,
    WithdrawPublication,
)
from app.application.scan_storage_retention import (
    POLICY_VERSION,
    PaperRetentionSettings,
)
from pydantic import ValidationError


@pytest.mark.parametrize("reason", ["", " ", "\n\t", "x" * 501, None, 1])
def test_invalid_reason(reason):
    with pytest.raises(ValidationError):
        WithdrawPublication(reason=reason)


def test_trim_and_bound():
    assert WithdrawPublication(reason=" \n correction \t").reason == "correction"
    assert len(WithdrawPublication(reason="x" * 500).reason) == 500
    # Bound applies to the normalized reason.
    assert len(WithdrawPublication(reason=" " + "x" * 500 + " ").reason) == 500


@pytest.mark.parametrize("count", [0, 501])
def test_bulk_bound(count):
    with pytest.raises(ValidationError):
        SelectedWithdrawals(
            reason="correction",
            entries=[
                WithdrawalEntry(paper_submission_id=uuid4(), publication_id=uuid4())
                for _ in range(count)
            ],
        )


def test_bulk_rejects_duplicate_papers():
    paper = uuid4()
    with pytest.raises(ValidationError, match="duplicate_paper_selection"):
        SelectedWithdrawals(
            reason="correction",
            entries=[
                WithdrawalEntry(paper_submission_id=paper, publication_id=uuid4()),
                WithdrawalEntry(paper_submission_id=paper, publication_id=uuid4()),
            ],
        )


@pytest.mark.parametrize("days", [0, -1, 3651, 1.5])
def test_grace_bounds(days):
    with pytest.raises(ValidationError):
        PaperRetentionSettings(withdrawal_page_grace_days=days)


def test_policy_version_and_default():
    assert POLICY_VERSION == "paper_storage_retention.v2"
    assert PaperRetentionSettings().withdrawal_page_grace_days == 30
