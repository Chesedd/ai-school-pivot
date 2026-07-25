"""Thin Content Bank HTTP routes."""

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.application.content_bank import ActorContext, CreateTaskCommand, CreateTaskService, SkillLinkInput, VersionContentInput
from app.config import Settings, get_settings
from app.db.session import async_session_factory
from app.infrastructure.repository import SQLAlchemyContentBankRepository, SQLAlchemyUnitOfWork
from app.presentation.schemas import CatalogResponse, TaskCreateRequest, TaskResponse

router = APIRouter(prefix="/api/content-bank")


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
