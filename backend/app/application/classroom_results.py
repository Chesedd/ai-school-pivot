"""Read-only projection boundary for classroom assessment results.

The boundary deliberately has no checking/assessment write dependency.  Its port
returns immutable records assembled from persisted snapshots.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID


class ClassroomResultsError(Exception):
    def __init__(self, code: str):
        self.code, self.status = code, 404
        super().__init__("Работа или участник не найдены либо больше недоступны.")


@dataclass(frozen=True)
class ResultActor:
    actor_id: UUID
    unrestricted: bool


class AssessmentResultsReadPort(Protocol):
    async def assignment_results(self, assignment_id: UUID, actor: ResultActor, offset: int, limit: int) -> dict | None: ...
    async def student_result(self, assignment_id: UUID, student_id: UUID, actor: ResultActor, attempt_no: int | None) -> dict | None: ...
    async def student_history(self, class_group_id: UUID, student_id: UUID, actor: ResultActor, offset: int, limit: int) -> dict | None: ...


def check_status(submitted: bool, run_status: str | None) -> str:
    if not submitted: return "not_submitted"
    if run_status is None: return "submitted"
    if run_status in {"pending", "running"}: return "checking"
    if run_status == "completed": return "checked"
    if run_status == "completed_with_review_required": return "review_required"
    return "check_failed"


def score_totals(results: list[object]) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if not results: return None, None, None
    maximum = sum((x.max_score for x in results), Decimal(0))
    suggested = None if any(x.score_suggested is None for x in results) else sum((x.score_suggested for x in results), Decimal(0))
    percent = suggested * Decimal(100) / maximum if suggested is not None and maximum > 0 else None
    return suggested, maximum, percent


class ClassroomAssessmentResultsService:
    def __init__(self, repository: AssessmentResultsReadPort): self.repository = repository
    async def assignment_results(self, assignment_id, actor, offset, limit):
        value = await self.repository.assignment_results(assignment_id, actor, offset, limit)
        if value is None: raise ClassroomResultsError("assignment_not_found")
        return value
    async def student_result(self, assignment_id, student_id, actor, attempt_no=None):
        value = await self.repository.student_result(assignment_id, student_id, actor, attempt_no)
        if value is None: raise ClassroomResultsError("participant_not_found" if attempt_no is None else "submission_not_found")
        return value
    async def student_history(self, class_group_id, student_id, actor, offset, limit):
        value = await self.repository.student_history(class_group_id, student_id, actor, offset, limit)
        if value is None: raise ClassroomResultsError("participant_not_found")
        return value
