"""SQLAlchemy adapters for Content Bank application ports."""

from __future__ import annotations

from types import TracebackType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.application.content_bank import ActorContext, CatalogRecord, CreateTaskCommand, SkillLinkDTO, TaskDTO, TaskVersionDTO
from app.infrastructure.models import Grade, Skill, Subject, Subtopic, Task, TaskSkillLink, TaskVersion, Topic


class SQLAlchemyContentBankRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_subject(self, value: UUID) -> CatalogRecord | None:
        row = await self.session.get(Subject, value)
        return CatalogRecord(row.id, row.name) if row else None

    async def get_grade(self, value: UUID) -> CatalogRecord | None:
        row = await self.session.get(Grade, value)
        return CatalogRecord(row.id, row.name) if row else None

    async def get_topic(self, value: UUID) -> CatalogRecord | None:
        row = await self.session.get(Topic, value)
        return CatalogRecord(row.id, row.name, subject_id=row.subject_id, grade_id=row.grade_id) if row else None

    async def get_subtopic(self, value: UUID) -> CatalogRecord | None:
        row = await self.session.get(Subtopic, value)
        return CatalogRecord(row.id, row.name, topic_id=row.topic_id) if row else None

    async def get_skills(self, values: set[UUID]) -> dict[UUID, CatalogRecord]:
        if not values:
            return {}
        result = await self.session.execute(select(Skill).options(selectinload(Skill.subtopic)).where(Skill.id.in_(values)))
        return {row.id: CatalogRecord(row.id, row.name, topic_id=row.subtopic.topic_id, subtopic_id=row.subtopic_id) for row in result.scalars()}

    async def create_task_with_initial_version(self, command: CreateTaskCommand, actor: ActorContext) -> TaskDTO:
        task = Task(subject_id=command.subject_id, grade_id=command.grade_id, topic_id=command.topic_id, subtopic_id=command.subtopic_id, created_by=actor.actor_id)
        self.session.add(task)
        await self.session.flush()
        content = command.initial_version
        version = TaskVersion(task_id=task.id, version_no=1, title=content.title, statement=content.statement, task_type=content.task_type, answer_format=content.answer_format, difficulty=content.difficulty, source=content.source, status="draft", created_by=actor.actor_id)
        self.session.add(version)
        await self.session.flush()
        skill_rows = await self.get_skills({link.skill_id for link in content.skills})
        links = [TaskSkillLink(task_version_id=version.id, skill_id=item.skill_id, weight=item.weight, is_primary=item.is_primary) for item in content.skills]
        self.session.add_all(links)
        await self.session.flush()
        await self.session.refresh(task)
        await self.session.refresh(version)
        return TaskDTO(task.id, task.subject_id, task.grade_id, task.topic_id, task.subtopic_id, task.created_by, task.created_at, TaskVersionDTO(version.id, 1, version.title, version.statement, version.task_type, version.answer_format, version.difficulty, version.source, version.status, version.created_by, version.created_at, tuple(SkillLinkDTO(link.id, link.skill_id, skill_rows[link.skill_id].name, link.weight, link.is_primary) for link in links)))

    async def catalog(self, name: str) -> list[CatalogRecord]:
        if name == "subjects":
            rows = (await self.session.execute(select(Subject).order_by(Subject.name))).scalars()
            return [CatalogRecord(x.id, x.name) for x in rows]
        if name == "grades":
            rows = (await self.session.execute(select(Grade).order_by(Grade.number))).scalars()
            return [CatalogRecord(x.id, x.name) for x in rows]
        if name == "topics":
            rows = (await self.session.execute(select(Topic).order_by(Topic.name))).scalars()
            return [CatalogRecord(x.id, x.name, subject_id=x.subject_id, grade_id=x.grade_id) for x in rows]
        if name == "subtopics":
            rows = (await self.session.execute(select(Subtopic).order_by(Subtopic.name))).scalars()
            return [CatalogRecord(x.id, x.name, topic_id=x.topic_id) for x in rows]
        rows = (await self.session.execute(select(Skill).options(selectinload(Skill.subtopic)).order_by(Skill.name))).scalars()
        return [CatalogRecord(x.id, x.name, topic_id=x.subtopic.topic_id, subtopic_id=x.subtopic_id) for x in rows]


class SQLAlchemyUnitOfWork:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory
        self.session: AsyncSession | None = None
        self.repository: SQLAlchemyContentBankRepository

    async def __aenter__(self) -> SQLAlchemyUnitOfWork:
        self.session = self.factory()
        self.repository = SQLAlchemyContentBankRepository(self.session)
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        assert self.session is not None
        if exc_type:
            await self.session.rollback()
        await self.session.close()

    async def commit(self) -> None:
        assert self.session is not None
        await self.session.commit()
