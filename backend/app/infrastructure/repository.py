"""SQLAlchemy adapters for Content Bank application ports."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from uuid import UUID

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.application.content_bank import AcceptedAnswerDTO, ActorContext, ArchiveResult, CatalogRecord, CatalogRef, ConflictError, CreateTaskCommand, EMPTY_METHODOLOGY, ExpectedSolutionDTO, HintDTO, LockedVersion, MethodologyDTO, RubricDTO, RubricItemDTO, SaveMethodologyCommand, SkillLinkDTO, TaskCard, TaskCardVersion, TaskDTO, TaskListItem, TaskListPage, TaskListQuery, TaskVersionDTO, TaskVersionSummary, TypicalErrorDTO, VersionState
from app.infrastructure.models import AcceptedAnswer, ExpectedSolution, Grade, Hint, Rubric, RubricItem, Skill, Subject, Subtopic, Task, TaskErrorLink, TaskSkillLink, TaskVersion, Topic, TypicalError


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

    async def get_task_card(self, task_id: UUID) -> TaskCard | None:
        """Load a card in a bounded number of queries, including archived tasks."""
        rows = (await self.session.execute(
            select(Task, Subject, Grade, Topic, Subtopic, TaskVersion)
            .join(Subject, Subject.id == Task.subject_id)
            .join(Grade, Grade.id == Task.grade_id)
            .join(Topic, and_(Topic.id == Task.topic_id, Topic.subject_id == Task.subject_id, Topic.grade_id == Task.grade_id))
            .outerjoin(Subtopic, and_(Subtopic.id == Task.subtopic_id, Subtopic.topic_id == Task.topic_id))
            .join(TaskVersion, TaskVersion.task_id == Task.id)
            .where(Task.id == task_id)
            .order_by(TaskVersion.version_no.desc(), TaskVersion.id.asc())
        )).all()
        if not rows:
            return None
        task, subject, grade, topic, subtopic, latest = rows[0]
        links = (await self.session.execute(
            select(TaskSkillLink, Skill)
            .join(Skill, Skill.id == TaskSkillLink.skill_id)
            .where(TaskSkillLink.task_version_id == latest.id)
            .order_by(TaskSkillLink.is_primary.desc(), Skill.name.asc(), Skill.id.asc())
        )).all()
        # The DB uniqueness constraint prevents duplicates; the guard also makes
        # the adapter robust when reading legacy/inconsistent data.
        seen: set[UUID] = set()
        skills = []
        for link, skill in links:
            if skill.id not in seen:
                seen.add(skill.id)
                skills.append(SkillLinkDTO(link.id, skill.id, skill.name, link.weight, link.is_primary))
        summaries = tuple(TaskVersionSummary(version.id, version.version_no, version.status, version.created_at, version.approved_at) for *_, version in rows)
        approved = next((summary for summary in summaries if summary.approved_at is not None), None)
        methodology = await self._get_methodology(latest.id)
        return TaskCard(
            task.id, CatalogRef(subject.id, subject.name), CatalogRef(grade.id, grade.name),
            CatalogRef(topic.id, topic.name), CatalogRef(subtopic.id, subtopic.name) if subtopic else None,
            task.created_by, task.created_at, task.archived_at,
            TaskCardVersion(latest.id, latest.version_no, latest.title, latest.statement, latest.task_type,
                latest.answer_format, latest.difficulty, latest.source, latest.status, tuple(skills),
                latest.created_by, latest.created_at, latest.approved_by, latest.approved_at, methodology),
            approved, summaries,
        )

    async def lock_version(self, task_version_id: UUID) -> LockedVersion | None:
        version = await self.session.scalar(select(TaskVersion).where(TaskVersion.id == task_version_id).with_for_update())
        if version is None:
            return None
        latest_no = await self.session.scalar(select(func.max(TaskVersion.version_no)).where(TaskVersion.task_id == version.task_id))
        skill_ids = frozenset((await self.session.scalars(select(TaskSkillLink.skill_id).where(TaskSkillLink.task_version_id == version.id))).all())
        return LockedVersion(version.id, version.answer_format, version.status, version.version_no == latest_no, skill_ids)

    async def lock_task_version(self, task_id: UUID, version_no: int) -> VersionState | None:
        task = await self.session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if task is None:
            return None
        version = await self.session.scalar(select(TaskVersion).where(TaskVersion.task_id == task_id, TaskVersion.version_no == version_no).with_for_update())
        if version is None:
            return None
        latest_no = await self.session.scalar(select(func.max(TaskVersion.version_no)).where(TaskVersion.task_id == task_id))
        links = (await self.session.execute(select(TaskSkillLink, Skill).join(Skill).where(TaskSkillLink.task_version_id == version.id).order_by(TaskSkillLink.id))).all()
        topic = await self.session.get(Topic, task.topic_id)
        subtopic = await self.session.get(Subtopic, task.subtopic_id) if task.subtopic_id else None
        skill_subtopics = {row.id: row for row in (await self.session.scalars(
            select(Subtopic).where(Subtopic.id.in_({skill.subtopic_id for _, skill in links}))
        )).all()} if links else {}
        classification_valid = bool(topic and topic.subject_id == task.subject_id and topic.grade_id == task.grade_id)
        classification_valid = classification_valid and (task.subtopic_id is None or bool(subtopic and subtopic.topic_id == task.topic_id))
        classification_valid = classification_valid and all(
            skill.subtopic_id in skill_subtopics
            and skill_subtopics[skill.subtopic_id].topic_id == task.topic_id
            and (task.subtopic_id is None or skill.subtopic_id == task.subtopic_id)
            for _, skill in links
        )
        return VersionState(task.id, version.id, version.version_no, version.status, version.statement, version.task_type,
            version.answer_format, version.created_at, version.created_by, version.approved_at, version.approved_by,
            task.archived_at, version.version_no == latest_no,
            tuple(SkillLinkDTO(link.id, skill.id, skill.name, link.weight, link.is_primary) for link, skill in links),
            await self._get_methodology(version.id), classification_valid)

    async def set_version_status(self, task_version_id: UUID, status: str, approved_at: datetime | None = None, approved_by: UUID | None = None) -> None:
        version = await self.session.get(TaskVersion, task_version_id)
        assert version is not None
        version.status = status
        if approved_at is not None:
            version.approved_at, version.approved_by = approved_at, approved_by
        await self.session.flush()

    async def archive_other_approved(self, task_id: UUID, except_version_id: UUID) -> None:
        rows = (await self.session.scalars(select(TaskVersion).where(TaskVersion.task_id == task_id, TaskVersion.status == "approved", TaskVersion.id != except_version_id).with_for_update())).all()
        for row in rows:
            row.status = "archived"
        await self.session.flush()

    async def clone_version(self, task_id: UUID, source_version_no: int, actor: ActorContext) -> VersionState:
        source = await self.session.scalar(select(TaskVersion).where(TaskVersion.task_id == task_id, TaskVersion.version_no == source_version_no))
        assert source is not None
        unfinished = await self.session.scalar(select(TaskVersion.id).where(TaskVersion.task_id == task_id, TaskVersion.version_no > source_version_no, TaskVersion.status.in_(("draft", "review"))))
        if unfinished is not None:
            raise ConflictError("У карточки уже есть незавершённая версия.")
        next_no = int((await self.session.scalar(select(func.max(TaskVersion.version_no)).where(TaskVersion.task_id == task_id))) or 0) + 1
        target = TaskVersion(task_id=task_id, version_no=next_no, title=source.title, statement=source.statement, task_type=source.task_type, answer_format=source.answer_format, difficulty=source.difficulty, source=source.source, status="draft", created_by=actor.actor_id)
        self.session.add(target); await self.session.flush()
        links = (await self.session.scalars(select(TaskSkillLink).where(TaskSkillLink.task_version_id == source.id))).all()
        self.session.add_all([TaskSkillLink(task_version_id=target.id, skill_id=x.skill_id, weight=x.weight, is_primary=x.is_primary) for x in links])
        solution = await self.session.scalar(select(ExpectedSolution).where(ExpectedSolution.task_version_id == source.id))
        if solution: self.session.add(ExpectedSolution(task_version_id=target.id, solution_text=solution.solution_text, final_answer=solution.final_answer, solution_steps_json=list(solution.solution_steps_json)))
        rubric = await self.session.scalar(select(Rubric).options(selectinload(Rubric.items)).where(Rubric.task_version_id == source.id))
        if rubric:
            copied = Rubric(task_version_id=target.id, max_score=rubric.max_score, grading_mode=rubric.grading_mode, notes=rubric.notes)
            self.session.add(copied); await self.session.flush()
            self.session.add_all([RubricItem(rubric_id=copied.id, criterion=x.criterion, max_points=x.max_points, required=x.required, common_failure=x.common_failure, order_index=x.order_index) for x in rubric.items])
        answers = (await self.session.scalars(select(AcceptedAnswer).where(AcceptedAnswer.task_version_id == source.id))).all()
        self.session.add_all([AcceptedAnswer(task_version_id=target.id, answer_value=x.answer_value, tolerance=x.tolerance, unit=x.unit, normalization_rule=x.normalization_rule) for x in answers])
        errors = (await self.session.scalars(select(TaskErrorLink).where(TaskErrorLink.task_version_id == source.id))).all()
        self.session.add_all([TaskErrorLink(task_version_id=target.id, typical_error_id=x.typical_error_id, detection_hint=x.detection_hint) for x in errors])
        hints = (await self.session.scalars(select(Hint).where(Hint.task_version_id == source.id))).all()
        self.session.add_all([Hint(task_version_id=target.id, level=x.level, hint_text=x.hint_text) for x in hints])
        await self.session.flush(); await self.session.refresh(target)
        result = await self.lock_task_version(task_id, next_no)
        assert result is not None
        return result

    async def archive_task_versions(self, task_id: UUID, archived_at: datetime) -> ArchiveResult | None:
        task = await self.session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if task is None: return None
        versions = (await self.session.scalars(select(TaskVersion).where(TaskVersion.task_id == task_id).order_by(TaskVersion.version_no.desc()).with_for_update())).all()
        if task.archived_at is None:
            task.archived_at = archived_at
            for version in versions:
                if version.status in {"draft", "review", "approved"}: version.status = "archived"
            await self.session.flush()
        return ArchiveResult(task.id, task.archived_at, versions[0].status)

    async def replace_methodology(self, command: SaveMethodologyCommand) -> MethodologyDTO:
        version_id = command.task_version_id
        rubric_ids = select(Rubric.id).where(Rubric.task_version_id == version_id)
        await self.session.execute(delete(RubricItem).where(RubricItem.rubric_id.in_(rubric_ids)))
        for model in (ExpectedSolution, Rubric, AcceptedAnswer, TaskErrorLink, Hint):
            await self.session.execute(delete(model).where(model.task_version_id == version_id))
        if command.expected_solution:
            value = command.expected_solution
            self.session.add(ExpectedSolution(task_version_id=version_id, solution_text=value.solution_text.strip(), final_answer=value.final_answer, solution_steps_json=[x.strip() for x in value.solution_steps]))
        if command.rubric:
            value = command.rubric
            rubric = Rubric(task_version_id=version_id, max_score=sum((x.max_points for x in value.items), start=0), grading_mode=value.grading_mode, notes=value.notes)
            self.session.add(rubric); await self.session.flush()
            self.session.add_all([RubricItem(rubric_id=rubric.id, criterion=x.criterion.strip(), max_points=x.max_points, required=x.required, common_failure=x.common_failure, order_index=i) for i, x in enumerate(value.items)])
        self.session.add_all([AcceptedAnswer(task_version_id=version_id, answer_value=x.answer_value.strip(), tolerance=x.tolerance, unit=x.unit, normalization_rule=x.normalization_rule) for x in command.accepted_answers])
        for value in command.typical_errors:
            existing = await self.session.scalar(select(TypicalError).where(TypicalError.skill_id == value.skill_id, TypicalError.code == value.code.strip()))
            definition = (value.title.strip(), value.description.strip(), value.severity, value.remediation_hint)
            if existing:
                if (existing.title, existing.description, existing.severity, existing.remediation_hint) != definition:
                    raise ConflictError("Определение типичной ошибки с таким кодом отличается.", "typical_error_definition_conflict")
                typical = existing
            else:
                typical = TypicalError(skill_id=value.skill_id, code=value.code.strip(), title=definition[0], description=definition[1], severity=definition[2], remediation_hint=definition[3])
                self.session.add(typical); await self.session.flush()
            self.session.add(TaskErrorLink(task_version_id=version_id, typical_error_id=typical.id, detection_hint=value.detection_hint))
        self.session.add_all([Hint(task_version_id=version_id, level=x.level, hint_text=x.hint_text.strip()) for x in command.hints])
        await self.session.flush()
        return await self._get_methodology(version_id)

    async def _get_methodology(self, version_id: UUID) -> MethodologyDTO:
        solution = await self.session.scalar(select(ExpectedSolution).where(ExpectedSolution.task_version_id == version_id))
        rubric = await self.session.scalar(select(Rubric).options(selectinload(Rubric.items)).where(Rubric.task_version_id == version_id))
        answers = (await self.session.scalars(select(AcceptedAnswer).where(AcceptedAnswer.task_version_id == version_id).order_by(AcceptedAnswer.id))).all()
        errors = (await self.session.execute(select(TaskErrorLink, TypicalError).join(TypicalError).where(TaskErrorLink.task_version_id == version_id).order_by(TaskErrorLink.id))).all()
        hints = (await self.session.scalars(select(Hint).where(Hint.task_version_id == version_id).order_by(Hint.level))).all()
        if not any((solution, rubric, answers, errors, hints)):
            return EMPTY_METHODOLOGY
        return MethodologyDTO(
            ExpectedSolutionDTO(solution.id, solution.solution_text, solution.final_answer, tuple(solution.solution_steps_json)) if solution else None,
            RubricDTO(rubric.id, rubric.grading_mode, rubric.max_score, rubric.notes, tuple(RubricItemDTO(x.id, x.criterion, x.max_points, x.required, x.common_failure, x.order_index) for x in sorted(rubric.items, key=lambda x: x.order_index))) if rubric else None,
            tuple(AcceptedAnswerDTO(x.id, x.answer_value, x.tolerance, x.unit, x.normalization_rule) for x in answers),
            tuple(TypicalErrorDTO(x.id, x.skill_id, x.code, x.title, x.description, x.severity, x.remediation_hint, link.detection_hint) for link, x in errors),
            tuple(HintDTO(x.id, x.level, x.hint_text) for x in hints),
        )

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
