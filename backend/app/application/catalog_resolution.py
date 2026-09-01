"""HTTP-independent, transaction-scoped administration of catalog proposals."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import exists, select, update

from app.infrastructure.catalog_repository import CatalogKind, MODELS, SQLAlchemyCatalogRepository
from app.infrastructure.models import Grade, Skill, Subject, Subtopic, Task, TaskErrorLink, TaskFolder, TaskSkillLink, TaskVersion, Tag, Topic, TypicalError


class CatalogResolutionError(Exception):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True, slots=True)
class ProposalView:
    kind: CatalogKind
    id: UUID
    name: str
    status: str
    proposed_by: UUID
    created_at: datetime
    updated_at: datetime
    number: int | None = None
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None


class CatalogResolutionService:
    def __init__(self, repository: SQLAlchemyCatalogRepository) -> None:
        self.repository = repository
        self.session = repository.session

    async def list_proposals(self, kind: CatalogKind | None, offset: int, limit: int) -> tuple[ProposalView, ...]:
        return tuple(self._view(kind_, row) for kind_, row in await self.repository.list_provisional(kind, offset, limit))

    async def confirm(self, kind: CatalogKind, proposal_id: UUID, actor_id: UUID) -> ProposalView:
        source = await self._source(kind, proposal_id)
        await self._canonicalize_parents(kind, source)
        source.status, source.resolved_by = "active", actor_id
        source.resolved_at, source.replacement_id, source.resolution_reason = datetime.now(timezone.utc), None, None
        await self.session.flush()
        return self._view(kind, source)

    async def merge(self, kind: CatalogKind, proposal_id: UUID, target_id: UUID, reason: str, actor_id: UUID) -> ProposalView:
        source = await self._source(kind, proposal_id)
        if target_id == source.id:
            raise CatalogResolutionError("catalog_merge_target_invalid", "Merge target is invalid.")
        target = await self.repository.get(kind, target_id)
        if target is None:
            raise CatalogResolutionError("catalog_merge_target_not_found", "Merge target was not found.", 404)
        if target.status != "active":
            raise CatalogResolutionError("catalog_merge_target_invalid", "Merge target must be active.")
        await self._canonicalize_parents(kind, source, target)
        await self._canonicalize_references(kind, source.id, target.id)
        source.status, source.replacement_id, source.resolved_by = "deprecated", target.id, actor_id
        source.resolved_at, source.resolution_reason = datetime.now(timezone.utc), reason.strip()
        await self.session.flush()
        return self._view(kind, source)

    async def reject(self, kind: CatalogKind, proposal_id: UUID, reason: str, actor_id: UUID) -> ProposalView:
        source = await self._source(kind, proposal_id)
        if await self._in_use(kind, source.id):
            raise CatalogResolutionError("catalog_proposal_in_use", "Catalog proposal is still in use.")
        source.status, source.replacement_id, source.resolved_by = "deprecated", None, actor_id
        source.resolved_at, source.resolution_reason = datetime.now(timezone.utc), reason.strip()
        await self.session.flush()
        return self._view(kind, source)

    async def _source(self, kind: CatalogKind, proposal_id: UUID):
        source = await self.repository.lock(kind, proposal_id)
        if source is None:
            raise CatalogResolutionError("catalog_proposal_not_found", "Catalog proposal was not found.", 404)
        if source.status != "provisional":
            raise CatalogResolutionError("catalog_proposal_already_resolved", "Catalog proposal was already resolved.")
        return source

    async def _effective(self, kind: CatalogKind, value: UUID):
        row = await self.repository.get(kind, value)
        if row is None:
            raise CatalogResolutionError("catalog_parent_rejected", "Catalog parent is unavailable.")
        if row.status == "active":
            return row
        if row.status == "provisional":
            raise CatalogResolutionError("catalog_parent_unresolved", "Catalog parent is unresolved.")
        if row.replacement_id is None:
            raise CatalogResolutionError("catalog_parent_rejected", "Catalog parent was rejected.")
        replacement = await self.repository.get(kind, row.replacement_id)
        if replacement is None or replacement.status != "active":
            raise CatalogResolutionError("catalog_parent_rejected", "Catalog parent has no active replacement.")
        return replacement

    async def _canonicalize_parents(self, kind: CatalogKind, source, target=None) -> None:
        specs = {"topic": (("subject", "subject_id"), ("grade", "grade_id")), "subtopic": (("topic", "topic_id"),), "skill": (("subtopic", "subtopic_id"),)}.get(kind, ())
        for parent_kind, field in specs:
            parent = await self._effective(parent_kind, getattr(source, field))
            if target is not None and getattr(target, field) != parent.id:
                raise CatalogResolutionError("catalog_merge_hierarchy_mismatch", "Merge target belongs to a different catalog hierarchy.")
            setattr(source, field, parent.id)

    async def _canonicalize_references(self, kind: CatalogKind, source: UUID, target: UUID) -> None:
        task_field = {"subject": Task.subject_id, "grade": Task.grade_id, "topic": Task.topic_id, "subtopic": Task.subtopic_id}.get(kind)
        if task_field is not None:
            referenced = select(Task.id).where(task_field == source)
            historical = select(TaskVersion.id).where(TaskVersion.task_id.in_(referenced), TaskVersion.status.in_(("approved", "archived"))).limit(1)
            if await self.session.scalar(historical) is not None:
                raise CatalogResolutionError("catalog_historical_reference_conflict", "Historical catalog references cannot be rewritten.")
            if kind == "subject" and await self.session.scalar(select(Task.id).where(Task.subject_id == source, Task.folder_id.is_not(None)).limit(1)):
                raise CatalogResolutionError("catalog_merge_reference_conflict", "Folder-bound task references require manual cleanup.")
            await self.session.execute(update(Task).where(task_field == source).values({task_field.key: target}))
        if kind == "skill":
            approved = await self.session.scalar(select(TaskSkillLink.id).join(TaskVersion).where(TaskSkillLink.skill_id == source, TaskVersion.status.in_(("approved", "archived"))).limit(1))
            approved_error = await self.session.scalar(select(TaskErrorLink.id).join(TaskVersion).join(TypicalError).where(TypicalError.skill_id == source, TaskVersion.status.in_(("approved", "archived"))).limit(1))
            if approved is not None or approved_error is not None:
                raise CatalogResolutionError("catalog_historical_reference_conflict", "Historical skill references cannot be rewritten.")
            duplicate = await self.session.scalar(select(TaskSkillLink.id).where(TaskSkillLink.skill_id == source, TaskSkillLink.task_version_id.in_(select(TaskSkillLink.task_version_id).where(TaskSkillLink.skill_id == target))).limit(1))
            if duplicate is not None:
                raise CatalogResolutionError("catalog_merge_reference_conflict", "Skill links conflict and require manual cleanup.")
            await self.session.execute(update(TaskSkillLink).where(TaskSkillLink.skill_id == source, TaskSkillLink.task_version_id.in_(select(TaskVersion.id).where(TaskVersion.status.in_(("draft", "review"))))).values(skill_id=target))
            if await self.session.scalar(select(TypicalError.id).where(TypicalError.skill_id == source, TypicalError.code.in_(select(TypicalError.code).where(TypicalError.skill_id == target))).limit(1)):
                raise CatalogResolutionError("catalog_merge_reference_conflict", "Methodology references conflict and require manual cleanup.")
            await self.session.execute(update(TypicalError).where(TypicalError.skill_id == source).values(skill_id=target))

    async def _in_use(self, kind: CatalogKind, value: UUID) -> bool:
        checks = []
        if kind == "subject": checks += [select(Topic.id).where(Topic.subject_id == value, Topic.status == "provisional"), select(Task.id).where(Task.subject_id == value), select(TaskFolder.id).where(TaskFolder.subject_id == value), select(Tag.id).where(Tag.subject_id == value)]
        elif kind == "grade": checks += [select(Topic.id).where(Topic.grade_id == value, Topic.status == "provisional"), select(Task.id).where(Task.grade_id == value)]
        elif kind == "topic": checks += [select(Subtopic.id).where(Subtopic.topic_id == value, Subtopic.status == "provisional"), select(Task.id).where(Task.topic_id == value)]
        elif kind == "subtopic": checks += [select(Skill.id).where(Skill.subtopic_id == value, Skill.status == "provisional"), select(Task.id).where(Task.subtopic_id == value)]
        else: checks += [select(TaskSkillLink.id).where(TaskSkillLink.skill_id == value), select(TypicalError.id).where(TypicalError.skill_id == value)]
        return any(await self.session.scalar(query.limit(1)) is not None for query in checks)

    @staticmethod
    def _view(kind: CatalogKind, row) -> ProposalView:
        return ProposalView(kind, row.id, row.name, row.status, row.proposed_by, row.created_at, row.updated_at,
            getattr(row, "number", None), getattr(row, "subject_id", None), getattr(row, "grade_id", None),
            getattr(row, "topic_id", None), getattr(row, "subtopic_id", None))
