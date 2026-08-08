"""SQLAlchemy persistence adapter for draft Assessment Core authoring."""
from __future__ import annotations

from datetime import datetime
from types import TracebackType
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.application.assessments import CreateAssessmentCommand
from app.infrastructure.assessment_models import Assessment, AssessmentAuditLog, AssessmentVariant


class SQLAlchemyAssessmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _options():
        return (selectinload(Assessment.variants).selectinload(AssessmentVariant.items),)

    async def list(self, status: str | None, offset: int, limit: int):
        base = select(Assessment)
        if status:
            base = base.where(Assessment.status == status)
        total = await self.session.scalar(select(func.count()).select_from(base.order_by(None).subquery()))
        rows = (await self.session.execute(base.options(*self._options()).order_by(Assessment.created_at.desc(), Assessment.id).offset(offset).limit(limit))).scalars().all()
        return {"items": rows, "total": total or 0, "offset": offset, "limit": limit}

    async def get(self, assessment_id: UUID):
        return (await self.session.execute(select(Assessment).options(*self._options()).where(Assessment.id == assessment_id))).scalar_one_or_none()

    async def create(self, command: CreateAssessmentCommand, actor_id: UUID):
        row = Assessment(title=command.title, description=command.description, created_by=actor_id, variants=[])
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def lock(self, assessment_id: UUID):
        return await self.session.scalar(select(Assessment).where(Assessment.id == assessment_id).with_for_update())

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
        return row

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

    async def touch(self, assessment_id: UUID):
        await self.session.execute(update(Assessment).where(Assessment.id == assessment_id).values(updated_at=func.clock_timestamp()))

    async def append_audit(self, assessment_id: UUID, event: str, actor_id: UUID, details: dict[str, object]):
        self.session.add(AssessmentAuditLog(aggregate_type="assessment", aggregate_id=assessment_id, event_type=event, actor_type="teacher", actor_id=actor_id, details=details))
        await self.session.flush()


class SQLAlchemyAssessmentUnitOfWork:
    def __init__(self, factory: async_sessionmaker[AsyncSession]):
        self.factory = factory

    async def __aenter__(self):
        self.session = self.factory()
        self.repository = SQLAlchemyAssessmentRepository(self.session)
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None):
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    async def commit(self):
        await self.session.commit()
