"""Deterministic, advisory quality assistance for human authoring review."""
from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.application.authoring import AuthoringRequestV1
from app.application.authoring_pipeline import (
    GeneratedTaskDraftV1, MAX_ANSWER, MAX_HINT, MAX_HINTS, MAX_OPTIONS,
    MAX_OPTION, MAX_SOLUTION, MAX_STATEMENT, MAX_TITLE, SolverResultV1, cross_check,
)
from app.infrastructure.authoring_models import AuthoringReview, AuthoringReviewAudit, AuthoringSession
from sqlalchemy import select


class QualityEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)


class AuthoringQualityCheckV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str
    severity: Literal["blocking", "warning", "info"]
    passed: bool
    message: str
    evidence: QualityEvidenceV1 = Field(default_factory=QualityEvidenceV1)


class CheckedArtifactIdentityV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: UUID
    review_id: UUID
    review_version: int
    request_fingerprint: str


class AuthoringQualityReportV1(BaseModel):
    """Immutable and bounded report containing no provider payloads or task text."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    report_version: Literal["authoring_quality_report.v1"] = "authoring_quality_report.v1"
    report_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_artifact: CheckedArtifactIdentityV1
    checks: tuple[AuthoringQualityCheckV1, ...] = Field(max_length=16)
    overall_status: Literal["passed", "warnings", "blocked"]


class DuplicateCheckProvider(Protocol):
    """Seam for a future deterministic duplicate implementation (no embeddings here)."""
    def check(self, draft: object) -> AuthoringQualityCheckV1: ...


class NotAvailableDuplicateCheckProvider:
    def check(self, draft: object) -> AuthoringQualityCheckV1:
        return _check("duplicate_detection", "info", True, "Duplicate detection is not available.",
                      provider="not_available")


def _check(code: str, severity: str, passed: bool, message: str, **evidence) -> AuthoringQualityCheckV1:
    return AuthoringQualityCheckV1(code=code, severity=severity, passed=passed, message=message,
        evidence=QualityEvidenceV1(metadata=evidence))


def calculate_quality_report(session: AuthoringSession, review: AuthoringReview,
                             duplicate_provider: DuplicateCheckProvider | None = None) -> AuthoringQualityReportV1:
    draft = review.draft if isinstance(review.draft, dict) else {}
    request = AuthoringRequestV1.model_validate_json(json.dumps(session.frozen_request))
    checks: list[AuthoringQualityCheckV1] = []

    required = ("title", "statement", "expected_answer") + (("solution",) if request.task_type in {"calculation", "problem"} else ())
    missing = tuple(name for name in required if not isinstance(draft.get(name), str) or not draft[name].strip())
    checks.append(_check("completeness", "blocking", not missing,
        "Required authoring content is complete." if not missing else "Required authoring content is incomplete.",
        missing_fields=",".join(missing), missing_count=len(missing)))

    options = draft.get("choice_options", [])
    hints = draft.get("hints", [])
    lengths = {name: len(draft.get(name, "")) if isinstance(draft.get(name), str) else -1
               for name in ("title", "statement", "expected_answer", "solution")}
    bounds_ok = (0 < lengths["title"] <= MAX_TITLE and 0 < lengths["statement"] <= MAX_STATEMENT
        and 0 < lengths["expected_answer"] <= MAX_ANSWER and 0 < lengths["solution"] <= MAX_SOLUTION
        and isinstance(options, list) and len(options) <= MAX_OPTIONS
        and all(isinstance(x, dict) and 0 < len(x.get("content", "")) <= MAX_OPTION for x in options)
        and isinstance(hints, list) and len(hints) <= MAX_HINTS
        and all(isinstance(x, str) and 0 < len(x) <= MAX_HINT for x in hints))
    checks.append(_check("bounds", "blocking", bounds_ok,
        "Content is within authoring bounds." if bounds_ok else "Content exceeds or violates authoring bounds.",
        title_length=lengths["title"], statement_length=lengths["statement"],
        answer_length=lengths["expected_answer"], solution_length=lengths["solution"],
        choice_count=len(options) if isinstance(options, list) else -1,
        hint_count=len(hints) if isinstance(hints, list) else -1))

    try:
        GeneratedTaskDraftV1.model_validate_json(json.dumps(
            {**draft, "schema_version": "generated_task_draft.v1"}))
        structure_ok = True
    except Exception:
        structure_ok = False
    checks.append(_check("valid_structure", "blocking", structure_ok,
        "Task structure is valid." if structure_ok else "Task structure is invalid."))

    catalog_ok = (draft.get("task_type"), draft.get("answer_format")) == (request.task_type, request.answer_format)
    checks.append(_check("catalog_consistency", "blocking", catalog_ok,
        "Task type and answer format match the frozen request." if catalog_ok else
        "Task type or answer format differs from the frozen request.",
        task_type_matches=draft.get("task_type") == request.task_type,
        answer_format_matches=draft.get("answer_format") == request.answer_format))

    proposed = session.solver_result
    try:
        candidate = GeneratedTaskDraftV1.model_validate_json(json.dumps(
            {**draft, "schema_version": "generated_task_draft.v1"}))
        solver = SolverResultV1.model_validate_json(json.dumps(proposed))
        result = cross_check(candidate, solver)
        answer_match = result.status == "validated"
        comparator = result.comparator
    except Exception:
        answer_match, comparator = False, "not_applicable"
    checks.append(_check("answer_consistency", "warning", answer_match,
        "Expected answer matches the solver proposal." if answer_match else
        "Expected answer does not match, or cannot be compared with, the solver proposal.",
        comparison="match" if answer_match else "mismatch", comparator=comparator))
    explanation_present = isinstance(draft.get("solution"), str) and bool(draft["solution"].strip())
    checks.append(_check("optional_explanation", "warning", explanation_present,
        "An explanation is available." if explanation_present else "Optional explanation is missing."))
    checks.append((duplicate_provider or NotAvailableDuplicateCheckProvider()).check(draft))

    status = "blocked" if any(not x.passed and x.severity == "blocking" for x in checks) else (
        "warnings" if any(not x.passed and x.severity == "warning" for x in checks) else "passed")
    identity = CheckedArtifactIdentityV1(session_id=session.id, review_id=review.id,
        review_version=review.version, request_fingerprint=session.request_fingerprint)
    canonical = json.dumps({"identity": identity.model_dump(mode="json"),
        "checks": [x.model_dump(mode="json") for x in checks]}, sort_keys=True, separators=(",", ":"))
    return AuthoringQualityReportV1(report_id=hashlib.sha256(canonical.encode()).hexdigest(),
        checked_artifact=identity, checks=tuple(checks), overall_status=status)


class AuthoringQualityService:
    def __init__(self, db): self.db = db

    async def get(self, session_id: UUID, owner_id: UUID) -> AuthoringQualityReportV1:
        session = await self.db.scalar(select(AuthoringSession).where(
            AuthoringSession.id == session_id, AuthoringSession.owner_id == owner_id))
        if session is None: self._error("authoring_session_not_found", 404)
        review = await self.db.scalar(select(AuthoringReview).where(
            AuthoringReview.session_id == session_id, AuthoringReview.owner_id == owner_id))
        if review is None: self._error("authoring_review_not_started", 409)
        report = calculate_quality_report(session, review)
        self.db.add(AuthoringReviewAudit(id=uuid4(), session_id=session_id, review_id=review.id,
            actor_id=owner_id, action="quality_report_created", review_version=review.version,
            details={"quality_report_id": report.report_id, "overall_status": report.overall_status}))
        await self.db.commit()
        return report

    @staticmethod
    def _error(code, status):
        from app.application.authoring_api import AuthoringApiError
        raise AuthoringApiError(code, status)
