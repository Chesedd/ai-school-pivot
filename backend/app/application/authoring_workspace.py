"""Safe, read-only projection for the future Authoring Workspace UI."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.application.authoring_api import AuthoringApiError
from app.application.authoring_quality import calculate_quality_report
from app.infrastructure.authoring_repository import AuthoringWorkspaceRecord, AuthoringWorkspaceRepository


class WorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkspaceSessionV1(WorkspaceModel):
    id: UUID
    lifecycle_status: str
    created_at: datetime
    owner: UUID


class WorkspaceGenerationV1(WorkspaceModel):
    generated_draft_available: bool
    generator_route: dict | None
    generator_attempt_status: str | None


class WorkspaceSolverV1(WorkspaceModel):
    solver_status: str | None
    validation_status: str | None
    semantic_result: dict | None


class WorkspaceReviewV1(WorkspaceModel):
    current_revision: int | None
    revision_count: int
    reviewer_state: str
    latest_changes_summary: dict


class WorkspaceFindingV1(WorkspaceModel):
    code: str
    message: str


class WorkspaceOverrideV1(WorkspaceModel):
    overridden: bool
    reason: str | None = None
    warning_codes: tuple[str, ...] = ()


class WorkspaceQualityV1(WorkspaceModel):
    quality_status: Literal["not_available", "passed", "warnings", "blocked"]
    blocking_findings: tuple[WorkspaceFindingV1, ...]
    warnings: tuple[WorkspaceFindingV1, ...]
    override_state: WorkspaceOverrideV1


class WorkspaceAcceptedRevisionV1(WorkspaceModel):
    id: UUID
    revision_number: int


class WorkspacePromotedTaskV1(WorkspaceModel):
    task_id: UUID
    task_version_id: UUID


class WorkspaceAcceptanceV1(WorkspaceModel):
    accepted_revision: WorkspaceAcceptedRevisionV1 | None
    promoted_task_reference: WorkspacePromotedTaskV1 | None


class WorkspaceCostV1(WorkspaceModel):
    currency: str
    amount: Decimal


class WorkspaceProviderUsageV1(WorkspaceModel):
    provider_id: str
    model_id: str
    role: str
    attempt_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class WorkspaceDiagnosticsV1(WorkspaceModel):
    attempt_count: int
    total_cost_by_currency: tuple[WorkspaceCostV1, ...]
    provider_usage_summary: tuple[WorkspaceProviderUsageV1, ...]


class AuthoringWorkspaceViewV1(WorkspaceModel):
    schema_version: Literal["authoring_workspace_view.v1"] = "authoring_workspace_view.v1"
    session: WorkspaceSessionV1
    generation: WorkspaceGenerationV1
    solver: WorkspaceSolverV1
    review: WorkspaceReviewV1
    quality: WorkspaceQualityV1
    acceptance: WorkspaceAcceptanceV1
    diagnostics: WorkspaceDiagnosticsV1


def workspace_view(record: AuthoringWorkspaceRecord) -> AuthoringWorkspaceViewV1:
    """Derive only reviewer-safe data; provider payloads and solver reasoning never enter the DTO."""
    row, attempts, review, revisions, audits, promotion = (
        record.session, record.attempts, record.review, record.revisions, record.review_audits, record.promotion)
    generator_attempts = [item for item in attempts if item.role == "generator"]
    latest_generator = generator_attempts[-1] if generator_attempts else None
    solver_status = row.solver_result.get("status") if isinstance(row.solver_result, dict) else None
    validation = row.validation_result if isinstance(row.validation_result, dict) else None

    latest_revision = revisions[-1] if revisions else None
    accepted = next((item for item in revisions if review and item.id == review.accepted_revision_id), None)
    override = next((item for item in reversed(audits) if item.action == "warning_overridden"), None)
    override_details = override.details if override and isinstance(override.details, dict) else {}

    if review is None:
        quality_status, blocking, warnings = "not_available", (), ()
    else:
        report = calculate_quality_report(row, review)
        quality_status = report.overall_status
        blocking = tuple(WorkspaceFindingV1(code=x.code, message=x.message) for x in report.checks
                         if not x.passed and x.severity == "blocking")
        warnings = tuple(WorkspaceFindingV1(code=x.code, message=x.message) for x in report.checks
                         if not x.passed and x.severity == "warning")

    costs: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    usage: dict[tuple[str, str, str], dict[str, int]] = {}
    for item in attempts:
        if item.cost_amount is not None and item.currency:
            costs[item.currency] += item.cost_amount
        key = (item.provider_id, item.model_id, item.role)
        totals = usage.setdefault(key, {"attempt_count": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0})
        totals["attempt_count"] += 1
        for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            totals[field] += getattr(item, field)

    return AuthoringWorkspaceViewV1(
        session=WorkspaceSessionV1(id=row.id, lifecycle_status=row.status, created_at=row.created_at, owner=row.owner_id),
        generation=WorkspaceGenerationV1(generated_draft_available=row.generated_draft is not None,
            generator_route=row.generator_route, generator_attempt_status=latest_generator.status if latest_generator else None),
        solver=WorkspaceSolverV1(solver_status=solver_status, validation_status=row.semantic_status,
            semantic_result=validation),
        review=WorkspaceReviewV1(current_revision=latest_revision.revision_number if latest_revision else None,
            revision_count=len(revisions), reviewer_state=review.state if review else "not_started",
            latest_changes_summary=latest_revision.change_summary if latest_revision else {}),
        quality=WorkspaceQualityV1(quality_status=quality_status, blocking_findings=blocking, warnings=warnings,
            override_state=WorkspaceOverrideV1(overridden=override is not None,
                reason=override_details.get("reason"), warning_codes=tuple(override_details.get("warning_codes", ())))),
        acceptance=WorkspaceAcceptanceV1(
            accepted_revision=WorkspaceAcceptedRevisionV1(id=accepted.id, revision_number=accepted.revision_number) if accepted else None,
            promoted_task_reference=WorkspacePromotedTaskV1(task_id=promotion.task_id,
                task_version_id=promotion.task_version_id) if promotion else None),
        diagnostics=WorkspaceDiagnosticsV1(attempt_count=len(attempts),
            total_cost_by_currency=tuple(WorkspaceCostV1(currency=key, amount=value) for key, value in sorted(costs.items())),
            provider_usage_summary=tuple(WorkspaceProviderUsageV1(provider_id=key[0], model_id=key[1], role=key[2],
                **values) for key, values in sorted(usage.items()))))


class AuthoringWorkspaceService:
    def __init__(self, db):
        self.repository = AuthoringWorkspaceRepository(db)

    async def get(self, session_id: UUID, owner_id: UUID) -> AuthoringWorkspaceViewV1:
        record = await self.repository.get(session_id, owner_id)
        if record is None:
            raise AuthoringApiError("authoring_session_not_found", 404)
        return workspace_view(record)
