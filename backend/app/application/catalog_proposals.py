"""Authenticated, HTTP-independent curriculum catalog proposal orchestration."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.domain.catalog import CatalogLifecycle
from app.infrastructure.catalog_repository import CatalogKind, SQLAlchemyCatalogRepository


ProposalOutcome = Literal["existing_active", "existing_provisional", "created_provisional"]


@dataclass(frozen=True, slots=True)
class CatalogProposalCommand:
    kind: CatalogKind
    name: str
    number: int | None = None
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CatalogProposalResult:
    kind: CatalogKind
    id: UUID
    name: str
    status: Literal["active", "provisional"]
    outcome: ProposalOutcome
    number: int | None = None
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None


class CatalogProposalError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code, self.status = code, status


LIVE_UNIQUE_CONSTRAINTS = {
    "subject": "uq_subjects_live_normalized_name",
    "grade": "uq_grades_live_number",
    "topic": "uq_topics_live_identity",
    "subtopic": "uq_subtopics_live_identity",
    "skill": "uq_skills_live_identity",
}


def _constraint_name(exc: IntegrityError) -> str | None:
    original = exc.orig
    # asyncpg exposes the diagnostic through SQLAlchemy's adapter as either
    # ``constraint_name`` directly or on the wrapped driver exception.
    return getattr(original, "constraint_name", None) or getattr(getattr(original, "__cause__", None), "constraint_name", None)


class CatalogProposalService:
    def __init__(self, repository: SQLAlchemyCatalogRepository) -> None:
        self.repository = repository

    async def propose_catalog_value(self, actor_id: UUID, command: CatalogProposalCommand) -> CatalogProposalResult:
        if not command.name.strip() or len(command.name.strip()) > 200:
            raise CatalogProposalError("invalid_catalog_proposal", "Catalog name is invalid.", 422)
        if command.kind == "grade" and (command.number is None or not 1 <= command.number <= 11):
            raise CatalogProposalError("invalid_catalog_proposal", "Grade number is invalid.", 422)
        await self._validate_parents(command)
        for lifecycle, outcome in (
            (CatalogLifecycle.ACTIVE, "existing_active"),
            (CatalogLifecycle.PROVISIONAL, "existing_provisional"),
        ):
            row = await self._find(command, lifecycle)
            if row is not None:
                return self._result(command.kind, row, outcome)

        try:
            async with self.repository.session.begin_nested():
                row = await self.repository.create_provisional(
                    command.kind, name=command.name.strip(), code=f"proposal-{uuid4().hex}",
                    proposed_by=actor_id, number=command.number, subject_id=command.subject_id,
                    grade_id=command.grade_id, topic_id=command.topic_id,
                    subtopic_id=command.subtopic_id,
                )
        except IntegrityError as exc:
            if _constraint_name(exc) != LIVE_UNIQUE_CONSTRAINTS[command.kind]:
                raise
            # The unique index is the race authority. Once the conflicting
            # statement returns, PostgreSQL has resolved the winner.
            row = await self._find(command, CatalogLifecycle.ACTIVE)
            if row is not None:
                return self._result(command.kind, row, "existing_active")
            row = await self._find(command, CatalogLifecycle.PROVISIONAL)
            if row is None:
                raise
            return self._result(command.kind, row, "existing_provisional")
        return self._result(command.kind, row, "created_provisional")

    async def _find(self, command: CatalogProposalCommand, status: CatalogLifecycle):
        return await self.repository.find_exact(
            command.kind, command.name, status=status, number=command.number,
            subject_id=command.subject_id, grade_id=command.grade_id,
            topic_id=command.topic_id, subtopic_id=command.subtopic_id,
        )

    async def _validate_parents(self, command: CatalogProposalCommand) -> None:
        requirements = {
            "topic": (("subject", command.subject_id), ("grade", command.grade_id)),
            "subtopic": (("topic", command.topic_id),),
            "skill": (("subtopic", command.subtopic_id),),
        }.get(command.kind, ())
        for kind, entity_id in requirements:
            row = await self.repository.get(kind, entity_id)  # DTO guarantees non-null IDs.
            if row is None:
                raise CatalogProposalError("catalog_parent_not_found", "Catalog parent was not found.", 404)
            if row.status == CatalogLifecycle.DEPRECATED.value:
                raise CatalogProposalError("catalog_parent_deprecated", "Deprecated catalog parents cannot receive proposals.", 409)

    @staticmethod
    def _result(kind: CatalogKind, row, outcome: ProposalOutcome) -> CatalogProposalResult:
        return CatalogProposalResult(
            kind, row.id, row.name, row.status, outcome,
            getattr(row, "number", None), getattr(row, "subject_id", None),
            getattr(row, "grade_id", None), getattr(row, "topic_id", None),
            getattr(row, "subtopic_id", None),
        )
