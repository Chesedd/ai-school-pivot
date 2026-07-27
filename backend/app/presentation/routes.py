"""Thin Content Bank HTTP routes."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.application.content_bank import ActorContext, CreateTaskCommand, CreateTaskService, GetTaskCardService, ListTasksService, SkillLinkInput, TaskListQuery, VersionContentInput
from app.config import Settings, get_settings
from app.db.session import async_session_factory
from app.infrastructure.repository import SQLAlchemyContentBankRepository, SQLAlchemyUnitOfWork
from app.presentation.schemas import CatalogResponse, TaskCardResponse, TaskCreateRequest, TaskListPageResponse, TaskResponse

router = APIRouter(prefix="/api/content-bank")


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
    sort_by: Literal["created_at", "title", "difficulty", "status", "version_no"] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> object:
    query = TaskListQuery(subject_id, grade_id, topic_id, subtopic_id, skill_id, task_type, difficulty, status, offset, limit, sort_by, sort_order)
    async with async_session_factory() as session:
        return await ListTasksService(SQLAlchemyContentBankRepository(session)).list_tasks(query)


@router.get("/tasks/{task_id}", response_model=TaskCardResponse)
async def get_task_card(task_id: UUID) -> object:
    async with async_session_factory() as session:
        return await GetTaskCardService(SQLAlchemyContentBankRepository(session)).get_task_card(task_id)


@router.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(payload: TaskCreateRequest, response: Response, settings: Settings = Depends(get_settings)) -> object:
    content = payload.initial_version
    command = CreateTaskCommand(payload.subject_id, payload.grade_id, payload.topic_id, payload.subtopic_id, VersionContentInput(content.title, content.statement, content.task_type, content.answer_format, content.difficulty, content.source, tuple(SkillLinkInput(x.skill_id, x.weight, x.is_primary) for x in content.skills)))
    # Temporary server-side development identity until authentication is introduced.
    result = await CreateTaskService(SQLAlchemyUnitOfWork(async_session_factory)).create_task(command, ActorContext(settings.content_bank_dev_actor_id))
    response.headers["Location"] = f"/api/content-bank/tasks/{result.id}"
    return result


@router.get("/catalog/{catalog_name}", response_model=CatalogResponse)
async def get_catalog(catalog_name: str) -> object:
    if catalog_name not in {"subjects", "grades", "topics", "subtopics", "skills"}:
        raise HTTPException(status_code=404, detail="Catalog not found")
    async with async_session_factory() as session:
        items = await SQLAlchemyContentBankRepository(session).catalog(catalog_name)
    return {"catalog": catalog_name, "items": items}
