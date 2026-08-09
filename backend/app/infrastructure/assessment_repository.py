"""SQLAlchemy persistence adapter for draft Assessment Core authoring."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import TracebackType
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.application.assessments import AssignmentRecord, AssessmentItemRecord, AssessmentRecord, AssessmentVariantRecord, CreateAssessmentCommand, HistoricalTaskVersion
from app.infrastructure.assessment_models import (Assignment, AssignmentParticipant, Assessment, AssessmentAuditLog,
    AssessmentItem, AssessmentVariant, ClassGroup, Student)
from app.infrastructure.models import Task, TaskVersion


class SQLAlchemyContentBankReadPort:
    """Content Bank eligibility adapter sharing the Assessment transaction."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_historical_version(self, version_id: UUID) -> HistoricalTaskVersion | None:
        """Read one concrete version without current lifecycle eligibility checks."""
        row = await self.session.get(TaskVersion, version_id)
        if row is None:
            return None
        return HistoricalTaskVersion(row.id, row.task_id, row.title, row.statement,
                                     row.task_type, row.answer_format)

    async def get_historical_versions(self, version_ids: tuple[UUID, ...]) -> dict[UUID, HistoricalTaskVersion]:
        """Batch historical read; lifecycle/archive state is intentionally ignored."""
        if not version_ids:
            return {}
        rows = (await self.session.scalars(select(TaskVersion).where(TaskVersion.id.in_(version_ids)))).all()
        return {row.id: HistoricalTaskVersion(row.id, row.task_id, row.title, row.statement,
                    row.task_type, row.answer_format) for row in rows}

    async def lock_new_usage(self, version_id: UUID) -> bool:
        task_id = await self.session.scalar(select(TaskVersion.task_id).where(TaskVersion.id == version_id))
        if task_id is None:
            return False
        task = await self.session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if task is None:
            return False
        version = await self.session.scalar(
            select(TaskVersion).where(TaskVersion.id == version_id, TaskVersion.task_id == task.id).with_for_update()
        )
        return version is not None and version.status == "approved" and task.archived_at is None

    async def lock_publication_usage(self, version_ids: tuple[UUID, ...]) -> bool:
        wanted = sorted(set(version_ids))
        pairs = (await self.session.execute(select(TaskVersion.id, TaskVersion.task_id).where(TaskVersion.id.in_(wanted)))).all()
        if len(pairs) != len(wanted):
            return False
        task_ids = sorted({pair.task_id for pair in pairs})
        tasks = (await self.session.scalars(select(Task).where(Task.id.in_(task_ids)).order_by(Task.id).with_for_update())).all()
        versions = (await self.session.scalars(select(TaskVersion).where(TaskVersion.id.in_(wanted)).order_by(TaskVersion.task_id, TaskVersion.id).with_for_update())).all()
        ownership = {pair.id: pair.task_id for pair in pairs}
        return (len(tasks) == len(task_ids) and len(versions) == len(wanted)
                and all(task.archived_at is None for task in tasks)
                and all(version.status == "approved" and ownership.get(version.id) == version.task_id for version in versions))


class SQLAlchemyAssessmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _options():
        return (selectinload(Assessment.variants).selectinload(AssessmentVariant.items),)

    @staticmethod
    def _item_record(row: AssessmentItem) -> AssessmentItemRecord:
        return AssessmentItemRecord(row.id, row.task_version_id, row.position,
                                    row.points.quantize(Decimal("0.01")))

    @staticmethod
    def _variant_record(row: AssessmentVariant) -> AssessmentVariantRecord:
        items = tuple(SQLAlchemyAssessmentRepository._item_record(item)
                      for item in sorted(row.items, key=lambda value: (value.position, value.id)))
        return AssessmentVariantRecord(row.id, row.name, row.position, items)

    @classmethod
    def _record(cls, row: Assessment) -> AssessmentRecord:
        variants = tuple(cls._variant_record(variant)
                         for variant in sorted(row.variants, key=lambda value: (value.position, value.id)))
        return AssessmentRecord(row.id, row.title, row.description, row.status, variants, row.created_at,
                                row.updated_at, row.published_at, row.published_by)

    async def list(self, status: str | None, offset: int, limit: int):
        base = select(Assessment)
        if status:
            base = base.where(Assessment.status == status)
        total = await self.session.scalar(select(func.count()).select_from(base.order_by(None).subquery()))
        rows = (await self.session.execute(base.options(*self._options()).order_by(Assessment.created_at.desc(), Assessment.id).offset(offset).limit(limit))).scalars().all()
        return {"items": [self._record(row) for row in rows], "total": total or 0, "offset": offset, "limit": limit}

    async def get(self, assessment_id: UUID):
        row = (await self.session.execute(select(Assessment).options(*self._options()).where(Assessment.id == assessment_id))).scalar_one_or_none()
        return self._record(row) if row is not None else None

    async def create(self, command: CreateAssessmentCommand, actor_id: UUID):
        row = Assessment(title=command.title, description=command.description, created_by=actor_id, variants=[])
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        # refresh expires unloaded relationships; creation nevertheless has a
        # domain-known empty composition, so materialize without touching it.
        return AssessmentRecord(row.id, row.title, row.description, row.status, (), row.created_at,
                                row.updated_at, row.published_at, row.published_by)

    async def lock(self, assessment_id: UUID):
        return await self.session.scalar(select(Assessment).where(Assessment.id == assessment_id).with_for_update())

    async def lock_composition(self, assessment_id: UUID):
        variants = (await self.session.scalars(select(AssessmentVariant).where(
            AssessmentVariant.assessment_id == assessment_id).order_by(AssessmentVariant.position, AssessmentVariant.id).with_for_update())).all()
        result = []
        for variant in variants:
            items = (await self.session.scalars(select(AssessmentItem).where(AssessmentItem.variant_id == variant.id)
                .order_by(AssessmentItem.position, AssessmentItem.id).with_for_update())).all()
            result.append(AssessmentVariantRecord(variant.id, variant.name, variant.position,
                tuple(self._item_record(item) for item in items)))
        return tuple(result)

    async def lock_group_students(self, group_id: UUID):
        group = await self.session.scalar(select(ClassGroup).where(
            ClassGroup.id == group_id, ClassGroup.archived_at.is_(None)).with_for_update())
        if group is None: return None
        return tuple((await self.session.scalars(select(Student.id).where(
            Student.class_group_id == group_id, Student.archived_at.is_(None)).order_by(Student.id).with_for_update())).all())

    async def database_clock(self):
        return await self.session.scalar(select(func.clock_timestamp()))

    async def mark_published(self, assessment_id: UUID, now: datetime, actor_id: UUID):
        await self.session.execute(update(Assessment).where(Assessment.id == assessment_id).values(
            status="published", published_at=now, published_by=actor_id, updated_at=now))

    async def create_assignment(self, command, actor_id: UUID, student_ids: tuple[UUID, ...]):
        row = Assignment(assessment_id=command.assessment_id, class_group_id=command.class_group_id,
            start_at=command.start_at, due_at=command.due_at, max_attempts=command.max_attempts, created_by=actor_id)
        self.session.add(row); await self.session.flush()
        self.session.add_all([AssignmentParticipant(assignment_id=row.id, student_id=value,
            assigned_variant_id=None, variant_assigned_at=None) for value in student_ids])
        await self.session.flush(); await self.session.refresh(row)
        return self._assignment_record(row, student_ids)

    @staticmethod
    def _assignment_record(row: Assignment, student_ids=()) -> AssignmentRecord:
        return AssignmentRecord(row.id, row.assessment_id, row.class_group_id, row.status, row.start_at,
            row.due_at, row.max_attempts, row.created_at, row.closed_at, len(student_ids), tuple(student_ids))

    async def get_assignment(self, assignment_id: UUID):
        row = await self.session.get(Assignment, assignment_id)
        if row is None: return None
        ids = tuple((await self.session.scalars(select(AssignmentParticipant.student_id).where(
            AssignmentParticipant.assignment_id == assignment_id).order_by(AssignmentParticipant.student_id))).all())
        return self._assignment_record(row, ids)

    async def lock_assignment(self, assignment_id: UUID):
        return await self.session.scalar(select(Assignment).where(Assignment.id == assignment_id).with_for_update())

    async def close_assignment(self, assignment_id: UUID, now: datetime, actor_id: UUID):
        await self.session.execute(update(Assignment).where(Assignment.id == assignment_id).values(
            status="closed", closed_at=now, closed_by=actor_id))

    async def update_metadata_cas(self, assessment_id: UUID, expected: datetime, values: dict[str, object]):
        statement = (update(Assessment).where(Assessment.id == assessment_id, Assessment.updated_at == expected)
                     .values(**values, updated_at=func.clock_timestamp()).returning(Assessment.id))
        row_id = await self.session.scalar(statement)
        if row_id is None:
            return None
        await self.session.flush()
        return await self.get(row_id)

    async def create_variant(self, assessment_id: UUID, name: str):
        duplicate = await self.session.scalar(select(AssessmentVariant.id).where(AssessmentVariant.assessment_id == assessment_id, AssessmentVariant.name == name))
        if duplicate is not None:
            return None
        position = (await self.session.scalar(select(func.coalesce(func.max(AssessmentVariant.position), 0)).where(AssessmentVariant.assessment_id == assessment_id))) + 1
        row = AssessmentVariant(assessment_id=assessment_id, name=name, position=position, items=[])
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            return None
        return AssessmentVariantRecord(row.id, row.name, row.position, ())

    async def delete_variant(self, assessment_id: UUID, variant_id: UUID):
        row = await self.session.scalar(select(AssessmentVariant).where(AssessmentVariant.id == variant_id, AssessmentVariant.assessment_id == assessment_id))
        if row is None:
            return None
        position = row.position
        await self.session.delete(row)
        await self.session.flush()
        later = (await self.session.execute(select(AssessmentVariant).where(
            AssessmentVariant.assessment_id == assessment_id,
            AssessmentVariant.position > position,
        ).order_by(AssessmentVariant.position).with_for_update())).scalars().all()
        for variant in later:
            variant.position -= 1
            await self.session.flush()
        return position

    async def get_variant(self, assessment_id: UUID, variant_id: UUID):
        return await self.session.scalar(select(AssessmentVariant).where(
            AssessmentVariant.id == variant_id, AssessmentVariant.assessment_id == assessment_id))

    async def create_item(self, variant_id: UUID, task_version_id: UUID, points):
        position = (await self.session.scalar(select(func.coalesce(func.max(AssessmentItem.position), 0)).where(
            AssessmentItem.variant_id == variant_id))) + 1
        row = AssessmentItem(variant_id=variant_id, task_version_id=task_version_id,
                             position=position, points=points)
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            return None
        return self._item_record(row)

    async def delete_item(self, variant_id: UUID, item_id: UUID):
        row = await self.session.scalar(select(AssessmentItem).where(
            AssessmentItem.id == item_id, AssessmentItem.variant_id == variant_id))
        if row is None:
            return None
        removed_position = row.position
        await self.session.delete(row)
        await self.session.flush()
        later = (await self.session.scalars(select(AssessmentItem).where(
            AssessmentItem.variant_id == variant_id, AssessmentItem.position > removed_position
        ).order_by(AssessmentItem.position).with_for_update())).all()
        for item in later:
            item.position -= 1
            await self.session.flush()
        return removed_position

    async def reorder_items(self, variant_id: UUID, item_ids: tuple[UUID, ...]):
        rows = (await self.session.scalars(select(AssessmentItem).where(
            AssessmentItem.variant_id == variant_id).order_by(AssessmentItem.position).with_for_update())).all()
        current = {row.id: row for row in rows}
        if len(item_ids) != len(rows) or len(set(item_ids)) != len(item_ids) or set(item_ids) != set(current):
            return None
        # Move each row into a disjoint positive range before assigning 1..N;
        # this works with the immediate unique and positive check constraints.
        temporary_start = max((row.position for row in rows), default=0) + 1
        for offset, item_id in enumerate(item_ids):
            current[item_id].position = temporary_start + offset
            await self.session.flush()
        for position, item_id in enumerate(item_ids, 1):
            current[item_id].position = position
            await self.session.flush()
        variant = await self.session.scalar(select(AssessmentVariant).options(
            selectinload(AssessmentVariant.items)).where(AssessmentVariant.id == variant_id))
        return self._variant_record(variant)

    async def update_item_points(self, variant_id: UUID, item_id: UUID, points):
        row = await self.session.scalar(select(AssessmentItem).where(
            AssessmentItem.id == item_id, AssessmentItem.variant_id == variant_id))
        if row is None:
            return None
        row.points = points
        await self.session.flush()
        return self._item_record(row)

    async def touch(self, assessment_id: UUID):
        await self.session.execute(update(Assessment).where(Assessment.id == assessment_id).values(updated_at=func.clock_timestamp()))

    async def append_audit(self, assessment_id: UUID, event: str, actor_id: UUID, details: dict[str, object], aggregate_type: str = "assessment"):
        self.session.add(AssessmentAuditLog(aggregate_type=aggregate_type, aggregate_id=assessment_id, event_type=event, actor_type="teacher", actor_id=actor_id, details=details))
        await self.session.flush()


class SQLAlchemyAssessmentUnitOfWork:
    def __init__(self, factory: async_sessionmaker[AsyncSession]):
        self.factory = factory

    async def __aenter__(self):
        self.session = self.factory()
        self.repository = SQLAlchemyAssessmentRepository(self.session)
        self.content_bank = SQLAlchemyContentBankReadPort(self.session)
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None):
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    async def commit(self):
        await self.session.commit()
