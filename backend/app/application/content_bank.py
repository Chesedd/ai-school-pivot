"""Create-task command, DTOs, ports, validation, and use case."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

TASK_TYPES = frozenset({"test", "calculation", "problem", "open_question", "essay"})
DIFFICULTIES = frozenset({"basic", "standard", "advanced"})
STATUSES = frozenset({"draft", "review", "approved", "archived"})
SORT_FIELDS = frozenset({"created_at", "title", "difficulty", "status", "version_no"})
SORT_ORDERS = frozenset({"asc", "desc"})


@dataclass(frozen=True)
class ActorContext:
    actor_id: UUID
    actor_type: str = "development"


@dataclass(frozen=True)
class SkillLinkInput:
    skill_id: UUID
    weight: Decimal
    is_primary: bool


@dataclass(frozen=True)
class VersionContentInput:
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: str
    source: str | None
    skills: tuple[SkillLinkInput, ...]


@dataclass(frozen=True)
class CreateTaskCommand:
    subject_id: UUID
    grade_id: UUID
    topic_id: UUID
    subtopic_id: UUID | None
    initial_version: VersionContentInput


@dataclass(frozen=True)
class CatalogRecord:
    id: UUID
    name: str
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None


@dataclass(frozen=True)
class SkillLinkDTO:
    id: UUID
    skill_id: UUID
    name: str
    weight: Decimal
    is_primary: bool


@dataclass(frozen=True)
class TaskVersionDTO:
    id: UUID
    version_no: int
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: str
    source: str | None
    status: str
    created_by: UUID
    created_at: datetime
    skills: tuple[SkillLinkDTO, ...]


@dataclass(frozen=True)
class TaskDTO:
    id: UUID
    subject_id: UUID
    grade_id: UUID
    topic_id: UUID
    subtopic_id: UUID | None
    created_by: UUID
    created_at: datetime
    initial_version: TaskVersionDTO


@dataclass(frozen=True)
class TaskListQuery:
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None
    skill_id: UUID | None = None
    task_type: str | None = None
    difficulty: str | None = None
    status: str | None = None
    offset: int = 0
    limit: int = 20
    sort_by: str = "created_at"
    sort_order: str = "desc"


@dataclass(frozen=True)
class TaskListItem:
    task_id: UUID
    subject_id: UUID
    subject_name: str
    grade_id: UUID
    grade_name: str
    topic_id: UUID
    topic_name: str
    subtopic_id: UUID | None
    subtopic_name: str | None
    latest_version_id: UUID
    version_no: int
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: str
    status: str
    primary_skill_id: UUID | None
    primary_skill_name: str | None
    created_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True)
class TaskListPage:
    items: tuple[TaskListItem, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True)
class CatalogRef:
    id: UUID
    name: str


@dataclass(frozen=True)
class TaskVersionSummary:
    id: UUID
    version_no: int
    status: str
    created_at: datetime
    approved_at: datetime | None


@dataclass(frozen=True)
class TaskCardVersion:
    id: UUID
    version_no: int
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: str
    source: str | None
    status: str
    skills: tuple[SkillLinkDTO, ...]
    created_by: UUID
    created_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None


@dataclass(frozen=True)
class TaskCard:
    id: UUID
    subject: CatalogRef
    grade: CatalogRef
    topic: CatalogRef
    subtopic: CatalogRef | None
    created_by: UUID
    created_at: datetime
    archived_at: datetime | None
    latest_version: TaskCardVersion
    approved_version: TaskVersionSummary | None
    versions: tuple[TaskVersionSummary, ...]


@dataclass(frozen=True)
class ValidationDetail:
    field: str
    code: str
    message: str


class ApplicationError(Exception):
    def __init__(self, details: list[ValidationDetail], message: str = "Запрос содержит ошибки.") -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(Exception):
    """An application-level missing aggregate error."""


class ContentBankRepository(Protocol):
    async def get_subject(self, value: UUID) -> CatalogRecord | None: ...
    async def get_grade(self, value: UUID) -> CatalogRecord | None: ...
    async def get_topic(self, value: UUID) -> CatalogRecord | None: ...
    async def get_subtopic(self, value: UUID) -> CatalogRecord | None: ...
    async def get_skills(self, values: set[UUID]) -> dict[UUID, CatalogRecord]: ...
    async def create_task_with_initial_version(self, command: CreateTaskCommand, actor: ActorContext) -> TaskDTO: ...
    async def list_tasks(self, query: TaskListQuery) -> TaskListPage: ...
    async def get_task_card(self, task_id: UUID) -> TaskCard | None: ...


class UnitOfWork(Protocol):
    repository: ContentBankRepository
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None: ...
    async def commit(self) -> None: ...


COMPATIBLE_FORMATS = {
    "test": {"single_choice", "multiple_choice"},
    "calculation": {"short_text", "number", "expression"},
    "problem": {"number", "expression", "long_text"},
    "open_question": {"short_text", "long_text"},
    "essay": {"long_text"},
}


class CreateTaskService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    async def create_task(self, command: CreateTaskCommand, actor: ActorContext) -> TaskDTO:
        details = self._validate_values(command)
        async with self.uow:
            repository = self.uow.repository
            subject = await repository.get_subject(command.subject_id)
            grade = await repository.get_grade(command.grade_id)
            topic = await repository.get_topic(command.topic_id)
            subtopic = await repository.get_subtopic(command.subtopic_id) if command.subtopic_id else None
            skill_ids = {link.skill_id for link in command.initial_version.skills}
            skills = await repository.get_skills(skill_ids)
            if subject is None:
                details.append(ValidationDetail("subject_id", "not_found", "Предмет не найден."))
            if grade is None:
                details.append(ValidationDetail("grade_id", "not_found", "Класс не найден."))
            if topic is None:
                details.append(ValidationDetail("topic_id", "not_found", "Тема не найдена."))
            elif topic.subject_id != command.subject_id or topic.grade_id != command.grade_id:
                details.append(ValidationDetail("topic_id", "invalid_relation", "Тема не относится к выбранным предмету и классу."))
            if command.subtopic_id and subtopic is None:
                details.append(ValidationDetail("subtopic_id", "not_found", "Подтема не найдена."))
            elif subtopic and subtopic.topic_id != command.topic_id:
                details.append(ValidationDetail("subtopic_id", "invalid_relation", "Подтема не относится к выбранной теме."))
            for index, link in enumerate(command.initial_version.skills):
                skill = skills.get(link.skill_id)
                field = f"initial_version.skills.{index}.skill_id"
                if skill is None:
                    details.append(ValidationDetail(field, "not_found", "Навык не найден."))
                elif command.subtopic_id and skill.subtopic_id != command.subtopic_id:
                    details.append(ValidationDetail(field, "invalid_relation", "Навык не относится к выбранной подтеме."))
                elif skill.topic_id != command.topic_id:
                    details.append(ValidationDetail(field, "invalid_relation", "Навык не относится к выбранной теме."))
            if details:
                raise ApplicationError(details)
            result = await repository.create_task_with_initial_version(command, actor)
            await self.uow.commit()
            return result

    @staticmethod
    def _validate_values(command: CreateTaskCommand) -> list[ValidationDetail]:
        links = command.initial_version.skills
        details: list[ValidationDetail] = []
        ids = [link.skill_id for link in links]
        if len(ids) != len(set(ids)):
            details.append(ValidationDetail("initial_version.skills", "duplicate", "Навыки не должны повторяться."))
        if sum(link.is_primary for link in links) != 1:
            details.append(ValidationDetail("initial_version.skills", "primary_count", "Нужен ровно один основной навык."))
        for index, link in enumerate(links):
            if link.weight <= 0 or link.weight > 1:
                details.append(ValidationDetail(f"initial_version.skills.{index}.weight", "range", "Вес должен быть больше 0 и не больше 1."))
        if sum((link.weight for link in links), Decimal("0")) != Decimal("1.0000"):
            details.append(ValidationDetail("initial_version.skills", "weight_sum", "Сумма весов должна быть равна 1.0000."))
        allowed = COMPATIBLE_FORMATS.get(command.initial_version.task_type, set())
        if command.initial_version.answer_format not in allowed:
            details.append(ValidationDetail("initial_version.answer_format", "incompatible", "Формат ответа несовместим с типом задания."))
        return details


class ListTasksService:
    def __init__(self, repository: ContentBankRepository) -> None:
        self.repository = repository

    async def list_tasks(self, query: TaskListQuery) -> TaskListPage:
        details: list[ValidationDetail] = []
        if query.offset < 0:
            details.append(ValidationDetail("offset", "range", "Offset должен быть не меньше 0."))
        if query.limit < 1 or query.limit > 100:
            details.append(ValidationDetail("limit", "range", "Limit должен быть от 1 до 100."))
        for field, value, allowed in (("task_type", query.task_type, TASK_TYPES), ("difficulty", query.difficulty, DIFFICULTIES), ("status", query.status, STATUSES)):
            if value is not None and value not in allowed:
                details.append(ValidationDetail(field, "enum", "Недопустимое значение."))
        if query.sort_by not in SORT_FIELDS:
            details.append(ValidationDetail("sort_by", "enum", "Недопустимое поле сортировки."))
        if query.sort_order not in SORT_ORDERS:
            details.append(ValidationDetail("sort_order", "enum", "Недопустимое направление сортировки."))
        if details:
            raise ApplicationError(details)
        return await self.repository.list_tasks(query)


class GetTaskCardService:
    def __init__(self, repository: ContentBankRepository) -> None:
        self.repository = repository

    async def get_task_card(self, task_id: UUID) -> TaskCard:
        card = await self.repository.get_task_card(task_id)
        if card is None:
            raise NotFoundError("Задание не найдено.")
        return card
