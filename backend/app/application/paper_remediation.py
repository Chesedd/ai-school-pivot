"""Fail-closed interpretation of immutable published paper signals."""

import json

from pydantic import ValidationError

from app.application.paper_result_publication import PublicationSnapshot
from app.application.remediation import RemediationError


def source_type(submission_id, paper_id, publication_id):
    if submission_id is not None and paper_id is None and publication_id is None:
        return "digital"
    if submission_id is None and paper_id is not None and publication_id is not None:
        return "paper"
    raise RemediationError("remediation_source_invalid")


def published_signals(publication, paper_id):
    try:
        snapshot = PublicationSnapshot.model_validate_json(
            json.dumps(publication.publication_snapshot)
        )
        if (
            snapshot.paper_submission_id != paper_id
            or snapshot.approved_review_revision_id
            != publication.approved_review_revision_id
            or len({x.assessment_item_id for x in snapshot.items})
            != len(snapshot.items)
            or len({x.finding_id for x in snapshot.findings}) != len(snapshot.findings)
            or any(
                not (
                    (x.status == "correct" and x.accepted_score == x.max_score)
                    or (
                        x.status == "partially_correct"
                        and 0 < x.accepted_score < x.max_score
                    )
                    or (
                        x.status in {"incorrect", "not_attempted"}
                        and x.accepted_score == 0
                    )
                )
                for x in snapshot.items
            )
            or sum(x.accepted_score for x in snapshot.items)
            != snapshot.grade.calculated_total_score
            or sum(x.max_score for x in snapshot.items)
            != snapshot.grade.total_max_score
        ):
            raise ValueError("invalid_snapshot")
        return snapshot, {
            x.assessment_item_id
            for x in snapshot.items
            if x.accepted_score < x.max_score
        }
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise RemediationError("remediation_source_publication_invalid") from None


def validate_signals(snapshot, eligible, item_ids, finding_ids):
    if not item_ids or not set(item_ids) <= eligible:
        raise RemediationError("remediation_paper_item_ineligible", 422)
    if not set(finding_ids) <= {x.finding_id for x in snapshot.findings}:
        raise RemediationError("remediation_publication_finding_not_found", 422)
    if len(set(item_ids)) != len(item_ids) or len(set(finding_ids)) != len(finding_ids):
        raise RemediationError("remediation_duplicate_signal", 422)
