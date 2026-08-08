"""Create-task command, DTOs, ports, validation, and use case."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from types import TracebackType
from typing import Literal, Protocol, Self
from uuid import UUID, uuid4

TASK_TYPES = frozenset({"test", "calculation", "problem", "open_question", "essay"})
STATUSES = frozenset({"draft", "review", "approved", "archived"})
AUDIT_ACTIONS = frozenset({"task_created", "methodology_updated", "submitted_for_review", "returned_to_draft", "version_approved", "version_created", "task_archived", "tag_added_to_version", "tag_removed_from_version"})
SORT_FIELDS = frozenset({"created_at", "updated_at", "title", "difficulty", "status", "version_no", "relevance"})
SORT_ORDERS = frozenset({"asc", "desc"})

# Phase 2.11A duplicate policy. Candidate retrieval is deliberately broader
# than the warning policy so filtering and limiting always happen afterwards.
DUPLICATE_CANDIDATE_THRESHOLD = 0.55
DUPLICATE_HIGH_SIMILARITY_THRESHOLD = 0.85
DUPLICATE_SKILL_SIMILARITY_THRESHOLD = 0.70
DUPLICATE_FINAL_ANSWER_SIMILARITY_THRESHOLD = 0.65
DuplicateReason = Literal["exact_statement", "high_statement_similarity", "same_primary_skill", "same_final_answer"]


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
    difficulty: int
    source: str | None
    skills: tuple[SkillLinkInput, ...]


@dataclass(frozen=True)
class CreateTaskCommand:
    subject_id: UUID
    grade_id: UUID
    topic_id: UUID
    subtopic_id: UUID | None
    initial_version: VersionContentInput
    folder_id: UUID | None = None
    tag_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class TagRefDTO:
    id: UUID
    name: str
    category_code: str
    subject_id: UUID | None
    status: str
    replacement: dict | None = None


@dataclass(frozen=True)
class CatalogRecord:
    id: UUID
    name: str
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None
    code: str | None = None
    number: int | None = None

@dataclass(frozen=True)
class ImportCatalogContext:
    subjects: dict[UUID, CatalogRecord]
    grades: dict[UUID, CatalogRecord]
    topics: dict[UUID, CatalogRecord]
    subtopics: dict[UUID, CatalogRecord]
    skills: dict[UUID, CatalogRecord]


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
    difficulty: int
    source: str | None
    status: str
    created_by: UUID
    created_at: datetime
    skills: tuple[SkillLinkDTO, ...]
    tags: tuple[TagRefDTO, ...] = ()


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
    duplicate_warnings: tuple["DuplicateCandidate", ...] = ()
    folder_id: UUID | None = None


@dataclass(frozen=True)
class DuplicateQuery:
    statement: str
    primary_skill_id: UUID
    final_answer: str | None = None
    exclude_task_id: UUID | None = None
    limit: int = 5


@dataclass(frozen=True)
class DuplicateCandidateRecord:
    task_id: UUID; task_version_id: UUID; version_no: int; title: str | None
    status: str; statement: str; statement_similarity: float
    primary_skill_id: UUID | None; final_answer: str | None


@dataclass(frozen=True)
class DuplicateCandidate:
    task_id: UUID; task_version_id: UUID; version_no: int; title: str | None
    status: str; statement: str; statement_similarity: float
    same_primary_skill: bool; same_final_answer: bool
    reasons: tuple[DuplicateReason, ...]


@dataclass(frozen=True)
class DuplicateResult:
    has_likely_duplicates: bool
    items: tuple[DuplicateCandidate, ...]


@dataclass(frozen=True)
class TaskListQuery:
    subject_id: UUID | None = None
    grade_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None
    skill_id: UUID | None = None
    task_type: str | None = None
    difficulty_min: int | None = None
    difficulty_max: int | None = None
    status: str | None = None
    offset: int = 0
    limit: int = 20
    sort_by: str | None = None
    sort_order: str = "desc"
    q: str | None = None
    folder_id: UUID | None = None
    folder_scope: Literal["direct", "subtree"] | None = None
    root_only: bool = False
    tag_ids: tuple[UUID, ...] = ()


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
    difficulty: int
    status: str
    primary_skill_id: UUID | None
    primary_skill_name: str | None
    created_at: datetime
    archived_at: datetime | None
    updated_at: datetime
    folder_id: UUID | None = None
    folder_name: str | None = None
    tags: tuple[TagRefDTO, ...] = ()


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
    tags: tuple[TagRefDTO, ...] = ()


@dataclass(frozen=True)
class TaskCardVersion:
    id: UUID
    version_no: int
    title: str | None
    statement: str
    task_type: str
    answer_format: str
    difficulty: int
    source: str | None
    status: str
    skills: tuple[SkillLinkDTO, ...]
    created_by: UUID
    created_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    methodology: MethodologyDTO
    updated_at: datetime
    tags: tuple[TagRefDTO, ...] = ()


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
    task_id: UUID | None = None
    version_no: int | None = None


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
    updated_at: datetime


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
    def __init__(self, message: str, code: str = "not_found") -> None:
        super().__init__(message)
        self.code = code

class ConflictError(Exception):
    def __init__(self, message: str, code: str = "conflict") -> None:
        super().__init__(message)
        self.code = code

class GoneError(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message); self.code = code


class IssuesError(Exception):
    """A command validation failure that carries every discovered issue."""

    def __init__(self, code: str, message: str, issues: list[ValidationDetail]) -> None:
        super().__init__(message)
        self.code, self.issues = code, issues


@dataclass(frozen=True)
class ValidationReport:
    valid_for_approval: bool
    issues: tuple[ValidationDetail, ...]


@dataclass(frozen=True)
class VersionState:
    task_id: UUID
    task_version_id: UUID
    version_no: int
    status: str
    statement: str
    task_type: str
    answer_format: str
    created_at: datetime
    created_by: UUID
    approved_at: datetime | None
    approved_by: UUID | None
    archived_at: datetime | None
    is_latest: bool
    skills: tuple[SkillLinkDTO, ...]
    methodology: MethodologyDTO
    classification_valid: bool = True


@dataclass(frozen=True)
class StatusCommandResult:
    task_id: UUID
    task_version_id: UUID
    version_no: int
    previous_status: str
    status: str
    created_at: datetime
    created_by: UUID
    approved_at: datetime | None
    approved_by: UUID | None
    validation: ValidationReport | None = None


@dataclass(frozen=True)
class CreateVersionCommand:
    task_id: UUID
    source_version_no: int


@dataclass(frozen=True)
class ArchiveResult:
    task_id: UUID
    archived_at: datetime
    latest_status: str
    latest_version_id: UUID | None = None
    latest_version_no: int | None = None
    previous_status: str | None = None
    changed: bool = True


@dataclass(frozen=True)
class AuditEventRecord:
    task_id: UUID
    task_version_id: UUID | None
    version_no: int | None
    action: str
    actor_id: UUID
    reason: str | None = None
    details: dict[str, object] | None = None


@dataclass(frozen=True)
class AuditEventDTO:
    id: UUID
    task_id: UUID
    task_version_id: UUID | None
    version_no: int | None
    action: str
    actor_id: UUID
    reason: str | None
    details: dict[str, object]
    occurred_at: datetime


@dataclass(frozen=True)
class AuditPage:
    items: tuple[AuditEventDTO, ...]
    total: int
    offset: int
    limit: int


class AuditWriter:
    """Append through the business transaction's repository; never commits."""
    def __init__(self, repository: ContentBankRepository) -> None:
        self.repository = repository

    async def write(self, event: AuditEventRecord) -> None:
        await self.repository.append_audit(event)


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
    async def lock_task_version(self, task_id: UUID, version_no: int) -> VersionState | None: ...
    async def set_version_status(self, task_version_id: UUID, status: str, approved_at: datetime | None = None, approved_by: UUID | None = None) -> None: ...
    async def archive_other_approved(self, task_id: UUID, except_version_id: UUID) -> None: ...
    async def clone_version(self, task_id: UUID, source_version_no: int, actor: ActorContext) -> VersionState: ...
    async def archive_task_versions(self, task_id: UUID, archived_at: datetime) -> ArchiveResult | None: ...
    async def append_audit(self, event: AuditEventRecord) -> None: ...
    async def list_audit(self, task_id: UUID, offset: int, limit: int, action: str | None) -> AuditPage | None: ...
    async def save_import_preview(self, preview: ImportPreviewRecord) -> None: ...
    async def get_import_preview_for_update(self, token: UUID) -> ImportPreviewRecord | None: ...
    async def mark_import_preview_committed(self, token: UUID, at: datetime) -> None: ...
    async def get_import_catalog_context(self, commands: tuple[CreateTaskCommand, ...]) -> ImportCatalogContext: ...
    async def find_duplicate_candidates(self, query: DuplicateQuery) -> tuple[DuplicateCandidateRecord, ...]: ...


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
        async with self.uow:
            primary = next((x.skill_id for x in command.initial_version.skills if x.is_primary), None)
            warnings = () if primary is None else (await DuplicateCheckService(self.uow.repository).check(
                DuplicateQuery(command.initial_version.statement, primary))).items
            result = await CreateTaskOperation(self.uow.repository).create(command, actor)
            result = replace(result, duplicate_warnings=warnings)
            await self.uow.commit()
            return result

    @staticmethod
    def _validate_values(command: CreateTaskCommand) -> list[ValidationDetail]:
        return CreateTaskOperation.validate_values(command)


class CreateTaskOperation:
    """Shared transaction-neutral task/v1/skills/audit operation."""
    def __init__(self, repository: ContentBankRepository) -> None: self.repository = repository

    async def create(self, command: CreateTaskCommand, actor: ActorContext, catalog: ImportCatalogContext | None = None) -> TaskDTO:
            details = self.validate_values(command)
            repository = self.repository
            if catalog is None:
                subject = await repository.get_subject(command.subject_id)
                grade = await repository.get_grade(command.grade_id)
                topic = await repository.get_topic(command.topic_id)
                subtopic = await repository.get_subtopic(command.subtopic_id) if command.subtopic_id else None
                skills = await repository.get_skills({link.skill_id for link in command.initial_version.skills})
            else:
                subject, grade = catalog.subjects.get(command.subject_id), catalog.grades.get(command.grade_id)
                topic = catalog.topics.get(command.topic_id)
                subtopic = catalog.subtopics.get(command.subtopic_id) if command.subtopic_id else None
                skills = catalog.skills
            if command.folder_id is not None and catalog is None:
                folder = await repository.get_folder(command.folder_id)
                if folder is None:
                    from app.application.folders import FolderDomainError
                    raise FolderDomainError("folder_not_found", "Папка не найдена.", {"folder_id":str(command.folder_id)}, 404)
                if folder.subject_id != command.subject_id:
                    from app.application.folders import FolderDomainError
                    raise FolderDomainError("task_folder_subject_mismatch", "Предмет задания и папки не совпадает.", {"task_id":None,"task_subject_id":str(command.subject_id),"folder_id":str(folder.id),"folder_subject_id":str(folder.subject_id)})
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
            await AuditWriter(repository).write(AuditEventRecord(result.id, result.initial_version.id, 1, "task_created", actor.actor_id, details={}))
            return result

    @staticmethod
    def validate_values(command: CreateTaskCommand) -> list[ValidationDetail]:
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


def normalize_duplicate_text(value: str) -> str:
    """Unicode case normalization with all whitespace runs collapsed."""
    return " ".join(value.strip().casefold().split())


def application_trigram_similarity(left: str, right: str) -> float:
    """Deterministic pg_trgm-compatible approximation for in-preview rows."""
    def trigrams(value: str) -> set[str]:
        words=normalize_duplicate_text(value).split()
        return {padded[i:i+3] for word in words for padded in ("  "+word+" ",) for i in range(len(padded)-2)}
    a,b=trigrams(left),trigrams(right)
    return 1.0 if a == b else (2.0*len(a & b)/(len(a)+len(b)) if a and b else 0.0)


class DuplicatePolicy:
    """Pure warning policy applied after PostgreSQL's indexed candidate scan."""
    @staticmethod
    def evaluate(query: DuplicateQuery, records: tuple[DuplicateCandidateRecord, ...]) -> tuple[DuplicateCandidate, ...]:
        statement = normalize_duplicate_text(query.statement)
        answer = normalize_duplicate_text(query.final_answer) if query.final_answer else ""
        found: list[DuplicateCandidate] = []
        for row in records:
            exact = normalize_duplicate_text(row.statement) == statement
            same_skill = row.primary_skill_id == query.primary_skill_id
            other_answer = normalize_duplicate_text(row.final_answer) if row.final_answer else ""
            same_answer = bool(answer and other_answer and answer == other_answer)
            similarity = max(0.0, min(1.0, row.statement_similarity))
            likely = (exact or similarity >= DUPLICATE_HIGH_SIMILARITY_THRESHOLD or
                (similarity >= DUPLICATE_SKILL_SIMILARITY_THRESHOLD and same_skill) or
                (similarity >= DUPLICATE_FINAL_ANSWER_SIMILARITY_THRESHOLD and same_answer))
            if not likely:
                continue
            reasons: list[DuplicateReason] = []
            if exact: reasons.append("exact_statement")
            if similarity >= DUPLICATE_HIGH_SIMILARITY_THRESHOLD: reasons.append("high_statement_similarity")
            if same_skill: reasons.append("same_primary_skill")
            if same_answer: reasons.append("same_final_answer")
            found.append(DuplicateCandidate(row.task_id,row.task_version_id,row.version_no,row.title,row.status,row.statement,
                round(similarity + 1e-12, 4),same_skill,same_answer,tuple(reasons)))
        found.sort(key=lambda x: ("exact_statement" not in x.reasons,-x.statement_similarity,
            not x.same_primary_skill,not x.same_final_answer,str(x.task_id)))
        return tuple(found[:query.limit])


class DuplicateCheckService:
    def __init__(self, repository: ContentBankRepository) -> None: self.repository = repository
    async def check(self, query: DuplicateQuery) -> DuplicateResult:
        statement = normalize_duplicate_text(query.statement)
        details=[]
        if not statement: details.append(ValidationDetail("statement","blank","Условие не может быть пустым."))
        if query.limit < 1 or query.limit > 20: details.append(ValidationDetail("limit","range","Limit должен быть от 1 до 20."))
        if details: raise ApplicationError(details)
        normalized = replace(query, statement=statement)
        records = await self.repository.find_duplicate_candidates(normalized)
        items = DuplicatePolicy.evaluate(normalized, records)
        return DuplicateResult(bool(items), items)


@dataclass(frozen=True)
class ImportRow:
    row_number: int
    command: CreateTaskCommand

@dataclass(frozen=True)
class ImportIssue:
    code: str; field: str; message: str; severity: str = "error"
    duplicate_candidates: tuple[DuplicateCandidate, ...] = ()
    duplicate_row_number: int | None = None

@dataclass(frozen=True)
class ImportPreviewRow:
    row_number: int; status: str; command: CreateTaskCommand; issues: tuple[ImportIssue, ...]

@dataclass(frozen=True)
class ImportPreviewRecord:
    import_token: UUID; format: str; actor_id: UUID; rows: tuple[ImportPreviewRow, ...]
    created_at: datetime; expires_at: datetime; committed_at: datetime | None = None

class ImportPreviewService:
    def __init__(self, uow: UnitOfWork, ttl_minutes: int = 30) -> None: self.uow, self.ttl_minutes = uow, ttl_minutes
    async def preview(self, format: str, rows: tuple[ImportRow, ...], actor: ActorContext) -> ImportPreviewRecord:
        if format not in {"csv", "xlsx"} or not rows or len(rows) > 500 or len({r.row_number for r in rows}) != len(rows):
            raise IssuesError("import_validation_error", "Импорт содержит ошибки.", [ValidationDetail("rows", "invalid", "Нужны 1–500 строк с уникальными row_number.")])
        now = datetime.now(timezone.utc); checked = []
        async with self.uow:
            op = CreateTaskOperation(self.uow.repository)
            catalog = await self.uow.repository.get_import_catalog_context(tuple(r.command for r in rows))
            for row in rows:
                issues = [ImportIssue(x.code, x.field, x.message) for x in op.validate_values(row.command)]
                # Catalog validation uses the same operation without writes via a dedicated check.
                issues += [ImportIssue(x.code, x.field, x.message) for x in self._catalog_issues(catalog, row.command)]
                checked.append(ImportPreviewRow(row.row_number, "invalid" if issues else "valid", row.command, tuple(issues)))
            # Invalid rows never incur a duplicate query. Warnings do not alter
            # row validity or commit eligibility.
            enriched=[]
            for current in checked:
                issues=list(current.issues)
                if current.status == "valid":
                    primary=next(x.skill_id for x in current.command.initial_version.skills if x.is_primary)
                    duplicates=await DuplicateCheckService(self.uow.repository).check(
                        DuplicateQuery(current.command.initial_version.statement,primary))
                    if duplicates.items:
                        issues.append(ImportIssue("possible_duplicate","statement","Найдены возможные дубликаты.","warning",duplicates.items))
                enriched.append(replace(current,issues=tuple(issues)))
            # Only the later row points to the earlier row: no symmetric warnings.
            for index,current in enumerate(enriched):
                if current.status != "valid": continue
                for previous in enriched[:index]:
                    if previous.status != "valid": continue
                    q=DuplicateQuery(current.command.initial_version.statement,
                        next(x.skill_id for x in current.command.initial_version.skills if x.is_primary))
                    p=previous.command
                    same_statement=normalize_duplicate_text(q.statement)==normalize_duplicate_text(p.initial_version.statement)
                    same_skill=q.primary_skill_id==next(x.skill_id for x in p.initial_version.skills if x.is_primary)
                    similarity=application_trigram_similarity(q.statement,p.initial_version.statement)
                    if same_statement or similarity >= DUPLICATE_HIGH_SIMILARITY_THRESHOLD or (same_skill and similarity >= DUPLICATE_SKILL_SIMILARITY_THRESHOLD):
                        issue=ImportIssue("possible_duplicate","statement","Возможный дубликат другой строки preview.","warning",(),previous.row_number)
                        enriched[index]=replace(current,issues=current.issues+(issue,)); current=enriched[index]
            checked=enriched
            record = ImportPreviewRecord(uuid4(), format, actor.actor_id, tuple(checked), now, now + __import__('datetime').timedelta(minutes=self.ttl_minutes))
            await self.uow.repository.save_import_preview(record); await self.uow.commit(); return record

    @staticmethod
    def _catalog_issues(catalog: ImportCatalogContext, command: CreateTaskCommand) -> list[ValidationDetail]:
        d=[]; subject=catalog.subjects.get(command.subject_id); grade=catalog.grades.get(command.grade_id); topic=catalog.topics.get(command.topic_id); sub=catalog.subtopics.get(command.subtopic_id) if command.subtopic_id else None; skills=catalog.skills
        if not subject:d.append(ValidationDetail("subject_id","not_found","Предмет не найден."))
        if not grade:d.append(ValidationDetail("grade_id","not_found","Класс не найден."))
        if not topic:d.append(ValidationDetail("topic_id","not_found","Тема не найдена."))
        elif topic.subject_id!=command.subject_id or topic.grade_id!=command.grade_id:d.append(ValidationDetail("topic_id","invalid_relation","Нарушена иерархия каталога."))
        if command.subtopic_id and not sub:d.append(ValidationDetail("subtopic_id","not_found","Подтема не найдена."))
        elif sub and sub.topic_id!=command.topic_id:d.append(ValidationDetail("subtopic_id","invalid_relation","Нарушена иерархия каталога."))
        for i,x in enumerate(command.initial_version.skills):
            s=skills.get(x.skill_id)
            if not s:d.append(ValidationDetail(f"initial_version.skills.{i}.skill_id","not_found","Навык не найден."))
            elif s.topic_id!=command.topic_id or (command.subtopic_id and s.subtopic_id!=command.subtopic_id):d.append(ValidationDetail(f"initial_version.skills.{i}.skill_id","invalid_relation","Нарушена иерархия каталога."))
        return d

class ImportCommitService:
    def __init__(self, uow: UnitOfWork) -> None: self.uow=uow
    async def commit(self, token: UUID, row_numbers: tuple[int,...], actor: ActorContext) -> tuple[tuple[int,TaskDTO],...]:
        if not row_numbers or len(row_numbers)>500 or len(set(row_numbers))!=len(row_numbers): raise IssuesError("import_validation_error","Некорректный выбор строк.",[ValidationDetail("row_numbers","invalid","Укажите уникальные номера строк.")])
        async with self.uow:
            p=await self.uow.repository.get_import_preview_for_update(token)
            if not p or p.actor_id!=actor.actor_id: raise NotFoundError("Preview импорта не найден.","import_token_not_found")
            now=datetime.now(timezone.utc)
            if p.committed_at: raise ConflictError("Token уже использован.","import_token_already_committed")
            if p.expires_at<=now: raise GoneError("Token истёк.","import_token_expired")
            by_no={r.row_number:r for r in p.rows}; selected=[]
            for n in row_numbers:
                r=by_no.get(n)
                if not r or r.status!="valid": raise IssuesError("import_validation_error","Некорректный выбор строк.",[ValidationDetail("row_numbers","invalid","Выбрана отсутствующая или невалидная строка.")])
                selected.append(r)
            result=[]; op=CreateTaskOperation(self.uow.repository)
            catalog=await self.uow.repository.get_import_catalog_context(tuple(r.command for r in selected))
            for r in selected: result.append((r.row_number,await op.create(r.command,actor,catalog)))
            await self.uow.repository.mark_import_preview_committed(token,now); await self.uow.commit(); return tuple(result)


class ListTasksService:
    def __init__(self, repository: ContentBankRepository) -> None:
        self.repository = repository

    @staticmethod
    def normalize_query(query: TaskListQuery) -> TaskListQuery:
        details: list[ValidationDetail] = []
        if query.offset < 0:
            details.append(ValidationDetail("offset", "range", "Offset должен быть не меньше 0."))
        if query.limit < 1 or query.limit > 100:
            details.append(ValidationDetail("limit", "range", "Limit должен быть от 1 до 100."))
        for field, value, allowed in (("task_type", query.task_type, TASK_TYPES), ("status", query.status, STATUSES)):
            if value is not None and value not in allowed:
                details.append(ValidationDetail(field, "enum", "Недопустимое значение."))
        for field, value in (("difficulty_min", query.difficulty_min), ("difficulty_max", query.difficulty_max)):
            if value is not None and not 1 <= value <= 100:
                details.append(ValidationDetail(field, "range", "Сложность должна быть от 1 до 100."))
        if query.difficulty_min is not None and query.difficulty_max is not None and query.difficulty_min > query.difficulty_max:
            details.append(ValidationDetail("difficulty_min", "range", "Минимальная сложность не может превышать максимальную."))
        if query.folder_scope is not None and query.folder_id is None:
            details.append(ValidationDetail("folder_scope", "requires_folder_id", "folder_scope требует folder_id."))
        if query.folder_id is not None and query.subject_id is None:
            details.append(ValidationDetail("folder_id", "requires_subject_id", "folder_id требует subject_id."))
        normalized_q = query.q.strip() if query.q else None
        normalized_q = normalized_q or None
        if normalized_q is not None and len(normalized_q) > 200:
            details.append(ValidationDetail("q", "max_length", "Поисковый запрос должен быть не длиннее 200 символов."))
        effective_sort = query.sort_by or ("relevance" if normalized_q else "created_at")
        if effective_sort not in SORT_FIELDS:
            details.append(ValidationDetail("sort_by", "enum", "Недопустимое поле сортировки."))
        if effective_sort == "relevance" and normalized_q is None:
            details.append(ValidationDetail("sort_by", "requires_q", "Сортировка по релевантности требует непустой q."))
        if query.sort_order not in SORT_ORDERS:
            details.append(ValidationDetail("sort_order", "enum", "Недопустимое направление сортировки."))
        if details:
            raise ApplicationError(details)
        return replace(query, q=normalized_q, sort_by=effective_sort)

    async def list_tasks(self, query: TaskListQuery) -> TaskListPage:
        return await self.repository.list_tasks(self.normalize_query(query))


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

    async def save(self, command: SaveMethodologyCommand, actor: ActorContext) -> MethodologyDTO:
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
            await AuditWriter(self.uow.repository).write(AuditEventRecord(
                version.task_id, version.id, version.version_no, "methodology_updated", actor.actor_id,
                details={"rubric_items_count": len(command.rubric.items) if command.rubric else 0,
                    "accepted_answers_count": len(command.accepted_answers), "hints_count": len(command.hints),
                    "typical_error_links_count": len(command.typical_errors)}))
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


def _structural_issues(version: VersionState) -> list[ValidationDetail]:
    issues: list[ValidationDetail] = []
    if not version.statement.strip():
        issues.append(ValidationDetail("statement", "missing_statement", "Добавьте условие задания."))
    if not version.skills:
        issues.append(ValidationDetail("skills", "missing_skill", "Добавьте хотя бы один навык."))
    if sum(skill.is_primary for skill in version.skills) != 1:
        issues.append(ValidationDetail("skills", "missing_primary_skill", "Укажите ровно один основной навык."))
    ids = [skill.skill_id for skill in version.skills]
    if len(ids) != len(set(ids)):
        issues.append(ValidationDetail("skills", "duplicate_skill", "Навыки не должны повторяться."))
    if any(skill.weight <= 0 or skill.weight > 1 for skill in version.skills) or sum(
        (skill.weight for skill in version.skills), Decimal("0")
    ) != Decimal("1.0000"):
        issues.append(ValidationDetail("skills", "invalid_skill_weights", "Веса должны быть в диапазоне (0, 1] и давать сумму 1.0000."))
    if version.answer_format not in COMPATIBLE_FORMATS.get(version.task_type, set()):
        issues.append(ValidationDetail("answer_format", "incompatible_answer_format", "Формат ответа несовместим с типом задания."))
    if not version.classification_valid:
        issues.append(ValidationDetail("classification", "invalid_classification", "Классификация карточки и навыков несогласована."))
    return issues


def _methodology_issues(version: VersionState) -> list[ValidationDetail]:
    methodology = version.methodology
    issues: list[ValidationDetail] = []
    if methodology.expected_solution is None or not methodology.expected_solution.solution_text.strip():
        issues.append(ValidationDetail("methodology.expected_solution", "missing_expected_solution", "Добавьте эталонное решение."))
    if methodology.rubric is None:
        issues.append(ValidationDetail("methodology.rubric", "missing_rubric", "Добавьте рубрику оценивания."))
    elif not methodology.rubric.items:
        issues.append(ValidationDetail("methodology.rubric.items", "missing_rubric_items", "Добавьте хотя бы один критерий оценивания."))
    return issues


class StatusCycleService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    async def submit_review(self, task_id: UUID, version_no: int, actor: ActorContext) -> StatusCommandResult:
        async with self.uow:
            version = await self._lock_active_latest(task_id, version_no, "draft")
            structural = _structural_issues(version)
            if structural:
                raise IssuesError("validation_error", "Версия структурно некорректна.", structural)
            warnings = _methodology_issues(version)
            await self.uow.repository.set_version_status(version.task_version_id, "review")
            await AuditWriter(self.uow.repository).write(AuditEventRecord(task_id, version.task_version_id, version.version_no, "submitted_for_review", actor.actor_id, details={"from_status": "draft", "to_status": "review"}))
            await self.uow.commit()
            return self._result(version, "review", ValidationReport(not warnings, tuple(warnings)))

    async def return_draft(self, task_id: UUID, version_no: int, reason: str, actor: ActorContext) -> StatusCommandResult:
        async with self.uow:
            version = await self._lock_active_latest(task_id, version_no, "review")
            await self.uow.repository.set_version_status(version.task_version_id, "draft")
            await AuditWriter(self.uow.repository).write(AuditEventRecord(task_id, version.task_version_id, version.version_no, "returned_to_draft", actor.actor_id, reason=reason, details={"from_status": "review", "to_status": "draft"}))
            await self.uow.commit()
            return self._result(version, "draft")

    async def approve(self, task_id: UUID, version_no: int, actor: ActorContext) -> StatusCommandResult:
        async with self.uow:
            version = await self._lock_active_latest(task_id, version_no, "review")
            issues = _structural_issues(version) + _methodology_issues(version)
            rubric = version.methodology.rubric
            if rubric is not None:
                if rubric.max_score <= 0:
                    issues.append(ValidationDetail("methodology.rubric.max_score", "invalid_rubric_max_score", "Максимальный балл должен быть больше нуля."))
                if any(item.max_points <= 0 for item in rubric.items):
                    issues.append(ValidationDetail("methodology.rubric.items", "invalid_rubric_item_points", "Баллы каждого критерия должны быть больше нуля."))
                if sum((item.max_points for item in rubric.items), Decimal("0")) != rubric.max_score:
                    issues.append(ValidationDetail("methodology.rubric.max_score", "rubric_score_mismatch", "Максимальный балл должен совпадать с суммой баллов критериев."))
            if issues:
                raise IssuesError("approval_requirements_not_met", "Версия не соответствует требованиям утверждения.", issues)
            now = datetime.now(timezone.utc)
            await self.uow.repository.archive_other_approved(task_id, version.task_version_id)
            await self.uow.repository.set_version_status(version.task_version_id, "approved", now, actor.actor_id)
            audit_details: dict[str, object] = {"from_status": "review", "to_status": "approved"}
            await AuditWriter(self.uow.repository).write(AuditEventRecord(task_id, version.task_version_id, version.version_no, "version_approved", actor.actor_id, details=audit_details))
            await self.uow.commit()
            result = self._result(version, "approved", ValidationReport(True, ()))
            return StatusCommandResult(**{**result.__dict__, "approved_at": now, "approved_by": actor.actor_id})

    async def _lock_active_latest(self, task_id: UUID, version_no: int, required: str) -> VersionState:
        version = await self.uow.repository.lock_task_version(task_id, version_no)
        if version is None:
            raise NotFoundError("Версия задания не найдена.")
        if version.archived_at is not None:
            raise ConflictError("Архивная карточка недоступна для изменений.")
        if not version.is_latest or version.status != required:
            raise ConflictError("Недопустимый переход статуса.", "invalid_status_transition")
        return version

    @staticmethod
    def _result(version: VersionState, status: str, validation: ValidationReport | None = None) -> StatusCommandResult:
        return StatusCommandResult(version.task_id, version.task_version_id, version.version_no, version.status, status,
            version.created_at, version.created_by, version.approved_at, version.approved_by, validation)


class CreateVersionService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    async def create(self, command: CreateVersionCommand, actor: ActorContext) -> VersionState:
        async with self.uow:
            source = await self.uow.repository.lock_task_version(command.task_id, command.source_version_no)
            if source is None:
                raise NotFoundError("Исходная версия задания не найдена.")
            if source.archived_at is not None:
                raise ConflictError("Архивная карточка недоступна для изменений.")
            if source.status != "approved" or not source.is_latest:
                raise ConflictError("Исходная версия не является последней утверждённой.", "invalid_source_version")
            result = await self.uow.repository.clone_version(command.task_id, command.source_version_no, actor)
            await AuditWriter(self.uow.repository).write(AuditEventRecord(command.task_id, result.task_version_id, result.version_no, "version_created", actor.actor_id, details={"source_version_no": command.source_version_no, "source_version_id": str(source.task_version_id)}))
            await self.uow.commit()
            return result


class ArchiveTaskService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    async def archive(self, task_id: UUID, actor: ActorContext, reason: str | None = None) -> ArchiveResult:
        async with self.uow:
            result = await self.uow.repository.archive_task_versions(task_id, datetime.now(timezone.utc))
            if result is None:
                raise NotFoundError("Задание не найдено.")
            if result.changed:
                await AuditWriter(self.uow.repository).write(AuditEventRecord(task_id, result.latest_version_id, result.latest_version_no, "task_archived", actor.actor_id, reason=reason, details={"from_status": result.previous_status, "to_status": "archived"}))
            await self.uow.commit()
            return result


class GetAuditService:
    def __init__(self, repository: ContentBankRepository) -> None:
        self.repository = repository

    async def get(self, task_id: UUID, offset: int, limit: int, action: str | None) -> AuditPage:
        page = await self.repository.list_audit(task_id, offset, limit, action)
        if page is None:
            raise NotFoundError("Задание не найдено.", "task_not_found")
        return page
