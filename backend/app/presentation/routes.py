"""Thin Content Bank HTTP routes."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.application.content_bank import AcceptedAnswerInput, ActorContext, ApplicationError, ArchiveTaskService, CreateTaskCommand, CreateTaskService, CreateVersionCommand, CreateVersionService, ExpectedSolutionInput, GetAuditService, GetTaskCardService, HintInput, ListTasksService, RubricInput, RubricItemInput, SaveMethodologyCommand, SaveMethodologyService, SkillLinkInput, StatusCycleService, TaskListQuery, TypicalErrorInput, ValidationDetail, VersionContentInput
from app.config import Settings, get_settings
from app.db.session import async_session_factory
from app.infrastructure.repository import SQLAlchemyContentBankRepository, SQLAlchemyUnitOfWork
from app.presentation.schemas import AuditPageResponse, ArchiveRequest, ArchiveResponse, CatalogResponse, CreatedVersionResponse, CreateVersionRequest, EmptyRequest, MethodologyPutRequest, MethodologyResponse, ReturnToDraftRequest, StatusCommandResponse, TaskCardResponse, TaskCreateRequest, TaskListPageResponse, TaskResponse

router = APIRouter(prefix="/api/content-bank")

@router.put("/task-versions/{task_version_id}/methodology", response_model=MethodologyResponse)
async def put_methodology(task_version_id: UUID, payload: MethodologyPutRequest, settings: Settings = Depends(get_settings)) -> object:
    expected = payload.expected_solution
    rubric = payload.rubric
    command = SaveMethodologyCommand(
        task_version_id,
        ExpectedSolutionInput(expected.solution_text, expected.final_answer, tuple(expected.solution_steps)) if expected else None,
        RubricInput(rubric.grading_mode, rubric.notes, tuple(RubricItemInput(x.criterion, x.max_points, x.required, x.common_failure) for x in rubric.items)) if rubric else None,
        tuple(AcceptedAnswerInput(x.answer_value, x.tolerance, x.unit, x.normalization_rule) for x in payload.accepted_answers),
        tuple(TypicalErrorInput(x.skill_id, x.code, x.title, x.description, x.severity, x.remediation_hint, x.detection_hint) for x in payload.typical_errors),
        tuple(HintInput(x.level, x.hint_text) for x in payload.hints),
    )
    return await SaveMethodologyService(SQLAlchemyUnitOfWork(async_session_factory)).save(command, ActorContext(settings.content_bank_dev_actor_id))


@router.get("/tasks", response_model=TaskListPageResponse)
async def list_tasks(
    subject_id: UUID | None = None, grade_id: UUID | None = None,
    topic_id: UUID | None = None, subtopic_id: UUID | None = None,
    skill_id: UUID | None = None,
    task_type: Literal["test", "calculation", "problem", "open_question", "essay"] | None = None,
    difficulty: Literal["basic", "standard", "advanced"] | None = None,
    status: Literal["draft", "review", "approved", "archived"] | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    sort_by: Literal["created_at", "updated_at", "title", "difficulty", "status", "version_no", "relevance"] | None = None,
    sort_order: Literal["asc", "desc"] = "desc",
    q: str | None = None,
) -> object:
    query = TaskListQuery(subject_id, grade_id, topic_id, subtopic_id, skill_id, task_type, difficulty, status, offset, limit, sort_by, sort_order, q)
    async with async_session_factory() as session:
        return await ListTasksService(SQLAlchemyContentBankRepository(session)).list_tasks(query)


@router.get("/tasks/{task_id}", response_model=TaskCardResponse)
async def get_task_card(task_id: UUID) -> object:
    async with async_session_factory() as session:
        return await GetTaskCardService(SQLAlchemyContentBankRepository(session)).get_task_card(task_id)


@router.get("/tasks/{task_id}/audit", response_model=AuditPageResponse)
async def get_task_audit(
    task_id: UUID,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    action: Literal["task_created", "methodology_updated", "submitted_for_review", "returned_to_draft", "version_approved", "version_created", "task_archived"] | None = None,
) -> object:
    async with async_session_factory() as session:
        return await GetAuditService(SQLAlchemyContentBankRepository(session)).get(task_id, offset, limit, action)


@router.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(payload: TaskCreateRequest, response: Response, settings: Settings = Depends(get_settings)) -> object:
    content = payload.initial_version
    command = CreateTaskCommand(payload.subject_id, payload.grade_id, payload.topic_id, payload.subtopic_id, VersionContentInput(content.title, content.statement, content.task_type, content.answer_format, content.difficulty, content.source, tuple(SkillLinkInput(x.skill_id, x.weight, x.is_primary) for x in content.skills)))
    # Temporary server-side development identity until authentication is introduced.
    result = await CreateTaskService(SQLAlchemyUnitOfWork(async_session_factory)).create_task(command, ActorContext(settings.content_bank_dev_actor_id))
    response.headers["Location"] = f"/api/content-bank/tasks/{result.id}"
    return result


@router.post("/tasks/{task_id}/versions/{version_no}/submit-review", response_model=StatusCommandResponse)
async def submit_review(task_id: UUID, version_no: int, payload: EmptyRequest, settings: Settings = Depends(get_settings)) -> object:
    return await StatusCycleService(SQLAlchemyUnitOfWork(async_session_factory)).submit_review(task_id, version_no, ActorContext(settings.content_bank_dev_actor_id))


@router.post("/tasks/{task_id}/versions/{version_no}/return-to-draft", response_model=StatusCommandResponse)
async def return_to_draft(task_id: UUID, version_no: int, payload: ReturnToDraftRequest, settings: Settings = Depends(get_settings)) -> object:
    if not payload.reason.strip():
        raise ApplicationError([ValidationDetail("reason", "blank", "Причина не может быть пустой.")])
    return await StatusCycleService(SQLAlchemyUnitOfWork(async_session_factory)).return_draft(task_id, version_no, payload.reason.strip(), ActorContext(settings.content_bank_dev_actor_id))


@router.post("/tasks/{task_id}/versions/{version_no}/approve", response_model=StatusCommandResponse)
async def approve(task_id: UUID, version_no: int, payload: EmptyRequest, settings: Settings = Depends(get_settings)) -> object:
    return await StatusCycleService(SQLAlchemyUnitOfWork(async_session_factory)).approve(task_id, version_no, ActorContext(settings.content_bank_dev_actor_id))


@router.post("/tasks/{task_id}/versions", response_model=CreatedVersionResponse, status_code=201)
async def create_version(task_id: UUID, payload: CreateVersionRequest, response: Response, settings: Settings = Depends(get_settings)) -> object:
    result = await CreateVersionService(SQLAlchemyUnitOfWork(async_session_factory)).create(CreateVersionCommand(task_id, payload.source_version_no), ActorContext(settings.content_bank_dev_actor_id))
    response.headers["Location"] = f"/api/content-bank/tasks/{task_id}"
    return result


@router.post("/tasks/{task_id}/archive", response_model=ArchiveResponse)
async def archive_task(task_id: UUID, payload: ArchiveRequest | None = None, settings: Settings = Depends(get_settings)) -> object:
    reason = payload.reason.strip() if payload and payload.reason else None
    return await ArchiveTaskService(SQLAlchemyUnitOfWork(async_session_factory)).archive(task_id, ActorContext(settings.content_bank_dev_actor_id), reason)


@router.get("/catalog/{catalog_name}", response_model=CatalogResponse)
async def get_catalog(catalog_name: str) -> object:
    if catalog_name not in {"subjects", "grades", "topics", "subtopics", "skills"}:
        raise HTTPException(status_code=404, detail="Catalog not found")
    async with async_session_factory() as session:
        items = await SQLAlchemyContentBankRepository(session).catalog(catalog_name)
    return {"catalog": catalog_name, "items": items}
