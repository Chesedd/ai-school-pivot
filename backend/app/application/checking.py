"""Pure persistence commands and validation for Checking Engine v1.

This module intentionally performs no intake, routing, checking, or provider calls.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID


class CheckingPersistenceError(Exception): pass
class InvalidPersistenceCommand(CheckingPersistenceError): pass
class IdempotencyConflict(CheckingPersistenceError): pass
class ActiveRunConflict(CheckingPersistenceError): pass
class SourceSubmissionNotFound(CheckingPersistenceError): pass
class ConcurrentConflict(CheckingPersistenceError): pass


@dataclass(frozen=True)
class CreateRunCommand:
    submission_id: UUID; request_key: str; request_hash: str; handoff_version: int
    input_snapshot: dict[str, Any]; input_fingerprint: str
    snapshot_schema_version: str; routing_version: str; checker_set_version: str
    threshold_policy_version: str; prompt_model_policy_version: str
    supersedes_run_id: UUID | None = None

    def validate(self) -> None:
        versions = (self.snapshot_schema_version, self.routing_version, self.checker_set_version,
                    self.threshold_policy_version, self.prompt_model_policy_version)
        if not self.request_key or self.request_key != self.request_key.strip() or len(self.request_key) > 128:
            raise InvalidPersistenceCommand("invalid request key")
        if self.handoff_version <= 0 or any(not value.strip() or len(value) > 64 for value in versions):
            raise InvalidPersistenceCommand("invalid version")
        if any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
               for value in (self.request_hash, self.input_fingerprint)):
            raise InvalidPersistenceCommand("invalid SHA-256")
        snapshot_submission = self.input_snapshot.get("submission_id")
        if snapshot_submission is not None and snapshot_submission != str(self.submission_id):
            raise InvalidPersistenceCommand("snapshot submission mismatch")


ALLOWED_TRANSITIONS = {"pending": {"running", "failed_terminal"}, "running": {"completed", "completed_with_review_required", "failed_retryable", "failed_terminal"}, "failed_retryable": {"pending"}}


def validate_transition(current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidPersistenceCommand(f"transition {current} -> {target} is not allowed")


FORBIDDEN_EVENT_KEYS = {"raw_answer", "normalized_answer", "provider_output", "raw_output", "student_id", "participant_id", "display_name", "email"}
ALLOWED_EVENT_KEYS = {"attempt_no", "retry_count", "checker_type", "result_status", "model_run_id", "item_count"}


def safe_event_details(details: dict[str, Any]) -> dict[str, Any]:
    if set(details) - ALLOWED_EVENT_KEYS or set(details) & FORBIDDEN_EVENT_KEYS:
        raise InvalidPersistenceCommand("unsafe event detail key")
    return dict(details)


def _snapshot_item(snapshot: dict[str, Any], item_id: UUID) -> dict[str, Any]:
    matches = [item for item in snapshot.get("items", ()) if item.get("assessment_item_id") == str(item_id)]
    if len(matches) != 1:
        raise InvalidPersistenceCommand("assessment item is absent or duplicated in snapshot")
    return matches[0]


def validate_result(snapshot: dict[str, Any], item_id: UUID, task_version_id: UUID,
                    max_score: Decimal, status: str, score: Decimal | None) -> None:
    item = _snapshot_item(snapshot, item_id)
    if item.get("task_version_id") != str(task_version_id): raise InvalidPersistenceCommand("task version mismatch")
    try: frozen = Decimal(str(item["points"]))
    except (KeyError, ValueError): raise InvalidPersistenceCommand("invalid frozen points") from None
    if frozen != max_score: raise InvalidPersistenceCommand("max score mismatch")
    valid = ((status == "correct" and score == max_score) or (status == "incorrect" and score == 0)
             or (status == "partially_correct" and score is not None and 0 < score < max_score)
             or (status in {"insufficient_rubric", "manual_required"} and score is None))
    if not valid: raise InvalidPersistenceCommand("score/status mismatch")


def validate_finding(snapshot: dict[str, Any], item_id: UUID, rubric_item_id: UUID | None,
                     typical_error_id: UUID | None, skill_id: UUID | None, evidence: dict[str, Any]) -> None:
    item = _snapshot_item(snapshot, item_id)
    allowlists = {"rubric_item_ids": rubric_item_id, "typical_error_ids": typical_error_id, "skill_ids": skill_id}
    for key, value in allowlists.items():
        if value is not None and str(value) not in item.get(key, ()): raise InvalidPersistenceCommand(f"{key} provenance is outside snapshot")
    if evidence.get("schema_version") is None or len(str(evidence)) > 16000: raise InvalidPersistenceCommand("invalid evidence")


def validate_model_result(model_run_id: UUID, model_item_id: UUID, result_run_id: UUID, result_item_id: UUID) -> None:
    if model_run_id != result_run_id or model_item_id != result_item_id:
        raise InvalidPersistenceCommand("model result belongs to another run/item")
