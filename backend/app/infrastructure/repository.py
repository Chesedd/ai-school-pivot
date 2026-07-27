"""SQLAlchemy adapters for Content Bank application ports."""

from __future__ import annotations

from types import TracebackType
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.application.content_bank import ActorContext, CatalogRecord, CreateTaskCommand, SkillLinkDTO, TaskDTO, TaskListItem, TaskListPage, TaskListQuery, TaskVersionDTO
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

    async def list_tasks(self, query: TaskListQuery) -> TaskListPage:
        latest_numbers = select(TaskVersion.task_id, func.max(TaskVersion.version_no).label("version_no")).group_by(TaskVersion.task_id).subquery()
        latest = TaskVersion.__table__.alias("latest_version")
        primary_link = TaskSkillLink.__table__.alias("primary_link")
        primary_skill = Skill.__table__.alias("primary_skill")
        columns = (
            Task.id, Task.subject_id, Subject.name, Task.grade_id, Grade.name,
            Task.topic_id, Topic.name, Task.subtopic_id, Subtopic.name,
            latest.c.id, latest.c.version_no, latest.c.title, latest.c.statement,
            latest.c.task_type, latest.c.answer_format, latest.c.difficulty,
            latest.c.status, primary_skill.c.id, primary_skill.c.name,
            Task.created_at, Task.archived_at,
        )
        base = (select(*columns).join(Subject, Subject.id == Task.subject_id)
            .join(Grade, Grade.id == Task.grade_id).join(Topic, Topic.id == Task.topic_id)
            .outerjoin(Subtopic, Subtopic.id == Task.subtopic_id)
            .join(latest_numbers, latest_numbers.c.task_id == Task.id)
            .join(latest, and_(latest.c.task_id == Task.id, latest.c.version_no == latest_numbers.c.version_no))
            .outerjoin(primary_link, and_(primary_link.c.task_version_id == latest.c.id, primary_link.c.is_primary.is_(True)))
            .outerjoin(primary_skill, primary_skill.c.id == primary_link.c.skill_id))
        if query.status == "archived":
            base = base.where(Task.archived_at.is_not(None))
        else:
            base = base.where(Task.archived_at.is_(None))
        filters = ((Task.subject_id, query.subject_id), (Task.grade_id, query.grade_id), (Task.topic_id, query.topic_id), (Task.subtopic_id, query.subtopic_id), (latest.c.task_type, query.task_type), (latest.c.difficulty, query.difficulty), (latest.c.status, query.status))
        for column, value in filters:
            if value is not None:
                base = base.where(column == value)
        if query.skill_id is not None:
            skill_match = select(TaskSkillLink.id).where(TaskSkillLink.task_version_id == latest.c.id, TaskSkillLink.skill_id == query.skill_id).exists()
            base = base.where(skill_match)
        total = int((await self.session.scalar(select(func.count()).select_from(base.order_by(None).subquery()))) or 0)
        sort_columns = {"created_at": Task.created_at, "title": latest.c.title, "difficulty": latest.c.difficulty, "status": latest.c.status, "version_no": latest.c.version_no}
        sort_column = sort_columns[query.sort_by]
        ordered = sort_column.asc().nulls_last() if query.sort_order == "asc" else sort_column.desc().nulls_last()
        rows = (await self.session.execute(base.order_by(ordered, Task.id.asc()).offset(query.offset).limit(query.limit))).all()
        return TaskListPage(tuple(TaskListItem(*row) for row in rows), total, query.offset, query.limit)

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
