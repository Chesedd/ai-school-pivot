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
    methodology: MethodologyDTO


@dataclass(frozen=True)
class ExpectedSolutionInput:
    solution_text: str
    final_answer: str | None
    solution_steps: tuple[str, ...]

@dataclass(frozen=True)
class RubricItemInput:
    criterion: str
    max_points: Decimal
    required: bool
    common_failure: str | None

@dataclass(frozen=True)
class RubricInput:
    grading_mode: str
    notes: str | None
    items: tuple[RubricItemInput, ...]

@dataclass(frozen=True)
class AcceptedAnswerInput:
    answer_value: str
    tolerance: Decimal | None
    unit: str | None
    normalization_rule: str | None

@dataclass(frozen=True)
class TypicalErrorInput:
    skill_id: UUID
    code: str
    title: str
    description: str
    severity: str
    remediation_hint: str | None
    detection_hint: str | None

@dataclass(frozen=True)
class HintInput:
    level: int
    hint_text: str

@dataclass(frozen=True)
class SaveMethodologyCommand:
    task_version_id: UUID
    expected_solution: ExpectedSolutionInput | None
    rubric: RubricInput | None
    accepted_answers: tuple[AcceptedAnswerInput, ...]
    typical_errors: tuple[TypicalErrorInput, ...]
    hints: tuple[HintInput, ...]

@dataclass(frozen=True)
class ExpectedSolutionDTO:
    id: UUID
    solution_text: str
    final_answer: str | None
    solution_steps: tuple[str, ...]

@dataclass(frozen=True)
class RubricItemDTO:
    id: UUID
    criterion: str
    max_points: Decimal
    required: bool
    common_failure: str | None
    order_index: int

@dataclass(frozen=True)
class RubricDTO:
    id: UUID
    grading_mode: str
    max_score: Decimal
    notes: str | None
    items: tuple[RubricItemDTO, ...]

@dataclass(frozen=True)
class AcceptedAnswerDTO:
    id: UUID
    answer_value: str
    tolerance: Decimal | None
    unit: str | None
    normalization_rule: str | None

@dataclass(frozen=True)
class TypicalErrorDTO:
    id: UUID
    skill_id: UUID
    code: str
    title: str
    description: str
    severity: str
    remediation_hint: str | None
    detection_hint: str | None

@dataclass(frozen=True)
class HintDTO:
    id: UUID
    level: int
    hint_text: str

@dataclass(frozen=True)
class MethodologyDTO:
    expected_solution: ExpectedSolutionDTO | None
    rubric: RubricDTO | None
    accepted_answers: tuple[AcceptedAnswerDTO, ...]
    typical_errors: tuple[TypicalErrorDTO, ...]
    hints: tuple[HintDTO, ...]

EMPTY_METHODOLOGY = MethodologyDTO(None, None, (), (), ())

@dataclass(frozen=True)
class LockedVersion:
    id: UUID
    answer_format: str
    status: str
    is_latest: bool
    skill_ids: frozenset[UUID]


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

class ConflictError(Exception):
    def __init__(self, message: str, code: str = "conflict") -> None:
        super().__init__(message)
        self.code = code


class ContentBankRepository(Protocol):
    async def get_subject(self, value: UUID) -> CatalogRecord | None: ...
    async def get_grade(self, value: UUID) -> CatalogRecord | None: ...
    async def get_topic(self, value: UUID) -> CatalogRecord | None: ...
    async def get_subtopic(self, value: UUID) -> CatalogRecord | None: ...
    async def get_skills(self, values: set[UUID]) -> dict[UUID, CatalogRecord]: ...
    async def create_task_with_initial_version(self, command: CreateTaskCommand, actor: ActorContext) -> TaskDTO: ...
    async def list_tasks(self, query: TaskListQuery) -> TaskListPage: ...
    async def get_task_card(self, task_id: UUID) -> TaskCard | None: ...
    async def lock_version(self, task_version_id: UUID) -> LockedVersion | None: ...
    async def replace_methodology(self, command: SaveMethodologyCommand) -> MethodologyDTO: ...


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


class SaveMethodologyService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    async def save(self, command: SaveMethodologyCommand) -> MethodologyDTO:
        details = self.validate(command)
        if details:
            raise ApplicationError(details)
        async with self.uow:
            version = await self.uow.repository.lock_version(command.task_version_id)
            if version is None:
                raise NotFoundError("Версия задания не найдена.")
            if version.status != "draft" or not version.is_latest:
                raise ConflictError("Изменять можно только последнюю draft-версию.")
            details = []
            if version.answer_format != "number":
                for index, answer in enumerate(command.accepted_answers):
                    if answer.tolerance is not None:
                        details.append(ValidationDetail(f"accepted_answers.{index}.tolerance", "not_allowed", "Допуск разрешён только для числового формата ответа."))
            for index, error in enumerate(command.typical_errors):
                if error.skill_id not in version.skill_ids:
                    details.append(ValidationDetail(f"typical_errors.{index}.skill_id", "invalid_relation", "Навык не связан с этой версией."))
            if details:
                raise ApplicationError(details)
            result = await self.uow.repository.replace_methodology(command)
            await self.uow.commit()
            return result

    @staticmethod
    def validate(command: SaveMethodologyCommand) -> list[ValidationDetail]:
        details: list[ValidationDetail] = []
        def text_required(value: str, field: str) -> None:
            if not value.strip(): details.append(ValidationDetail(field, "blank", "Поле не может быть пустым."))
        if command.expected_solution:
            text_required(command.expected_solution.solution_text, "expected_solution.solution_text")
            for i, step in enumerate(command.expected_solution.solution_steps): text_required(step, f"expected_solution.solution_steps.{i}")
        if command.rubric:
            criteria = []
            for i, item in enumerate(command.rubric.items):
                text_required(item.criterion, f"rubric.items.{i}.criterion")
                if item.max_points <= 0: details.append(ValidationDetail(f"rubric.items.{i}.max_points", "range", "Баллы должны быть больше нуля."))
                criteria.append(item.criterion.strip().casefold())
            if len(criteria) != len(set(criteria)): details.append(ValidationDetail("rubric.items", "duplicate", "Критерии не должны повторяться."))
        answers = []
        for i, answer in enumerate(command.accepted_answers):
            text_required(answer.answer_value, f"accepted_answers.{i}.answer_value")
            if answer.tolerance is not None and answer.tolerance < 0: details.append(ValidationDetail(f"accepted_answers.{i}.tolerance", "range", "Допуск не может быть отрицательным."))
            answers.append((answer.answer_value.strip().casefold(), answer.tolerance, answer.unit, answer.normalization_rule))
        if len(answers) != len(set(answers)): details.append(ValidationDetail("accepted_answers", "duplicate", "Допустимые ответы не должны повторяться."))
        levels = [hint.level for hint in command.hints]
        for i, hint in enumerate(command.hints): text_required(hint.hint_text, f"hints.{i}.hint_text")
        if levels != list(range(1, len(levels) + 1)): details.append(ValidationDetail("hints", "sequence", "Уровни подсказок должны образовывать последовательность 1..N."))
        keys = []
        for i, error in enumerate(command.typical_errors):
            for name in ("code", "title", "description"): text_required(getattr(error, name), f"typical_errors.{i}.{name}")
            keys.append((error.skill_id, error.code.strip().casefold()))
        if len(keys) != len(set(keys)): details.append(ValidationDetail("typical_errors", "duplicate", "Пары skill_id и code не должны повторяться."))
        return details
