"""Thin Content Bank HTTP routes."""

from typing import Annotated, Literal
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.application.folders import CreateFolderCommand, RenameFolderCommand, MoveFolderCommand, DeleteFolderCommand, MoveTaskCommand, FolderService, GetFolderTreeService
from app.application.content_bank import AcceptedAnswerInput, ChoiceOptionInput, ChoiceOptionRuleInput, ChoiceScoringPolicyInput, ActorContext, ApplicationError, ArchiveTaskService, CreateTaskCommand, CreateTaskService, CreateVersionCommand, CreateVersionService, DuplicateCheckService, DuplicateQuery, ExpectedSolutionInput, GetAuditService, GetTaskCardService, HintInput, ListTasksService, RubricInput, RubricItemInput, SaveMethodologyCommand, SaveMethodologyService, SkillLinkInput, StatusCycleService, TaskListQuery, TypicalErrorInput, ValidationDetail, VersionContentInput
from app.config import Settings, get_settings
from app.db.session import async_session_factory
from app.infrastructure.repository import SQLAlchemyContentBankRepository, SQLAlchemyUnitOfWork
from app.presentation.schemas import FolderCreateRequest, FolderRenameRequest, FolderMoveRequest, TaskLocationRequest, FolderSummaryResponse, FolderTreeResponse, TaskLocationResponse
from app.presentation.schemas import AuditPageResponse, ArchiveRequest, ArchiveResponse, CatalogResponse, CreatedVersionResponse, CreateVersionRequest, DuplicateCheckRequest, DuplicateCheckResponse, EmptyRequest, MethodologyPutRequest, MethodologyResponse, ReturnToDraftRequest, StatusCommandResponse, TaskCardResponse, TaskCreateRequest, TaskListPageResponse, TaskResponse
from app.presentation.schemas import TagCreateRequest, TagPatchRequest, TagDeprecateRequest, TagResponse, VersionTagsPutRequest, VersionTagsResponse
from app.application.managed_tags import ManagedTagService

router = APIRouter(prefix="/api/content-bank")

@router.get("/tag-categories")
async def tag_categories():
    async with async_session_factory() as session: return await ManagedTagService(session).categories()

@router.get("/tags/similar")
async def similar_tags(name: Annotated[str,Query(min_length=1,max_length=80)], exclude_tag_id: UUID|None=None, limit:Annotated[int,Query(ge=1,le=20)]=5):
    async with async_session_factory() as session: return await ManagedTagService(session).similar(name,exclude_tag_id,limit)

@router.get("/tags")
async def list_tags(q:Annotated[str|None,Query(max_length=80)]=None,subject_id:UUID|None=None,category_code:str|None=None,status:Literal["active","deprecated","all"]="active",offset:Annotated[int,Query(ge=0)]=0,limit:Annotated[int,Query(ge=1,le=100)]=20):
    async with async_session_factory() as session:return await ManagedTagService(session).list(q,subject_id,category_code,status,offset,limit)

@router.get("/tags/{tag_id}",response_model=TagResponse)
async def get_tag(tag_id:UUID):
    async with async_session_factory() as session:return __import__('app.application.managed_tags',fromlist=['serialize']).serialize(await ManagedTagService(session).get(tag_id))

@router.post("/admin/tags",response_model=TagResponse,status_code=201,summary="Trusted pilot only: production authentication/RBAC is required")
async def create_tag(payload:TagCreateRequest,response:Response,settings:Settings=Depends(get_settings)):
    async with async_session_factory() as session: result=await ManagedTagService(session).create(payload.category_code,payload.subject_id,payload.name,settings.content_bank_dev_actor_id)
    response.headers["Location"]=f"/api/content-bank/tags/{result['id']}";return result

@router.patch("/admin/tags/{tag_id}",response_model=TagResponse,summary="Trusted pilot only: production authentication/RBAC is required")
async def patch_tag(tag_id:UUID,payload:TagPatchRequest,settings:Settings=Depends(get_settings)):
    fields=payload.model_fields_set-{"expected_updated_at"}; values=payload.model_dump()
    async with async_session_factory() as session:return await ManagedTagService(session).patch(tag_id,payload.expected_updated_at,settings.content_bank_dev_actor_id,values,fields)

@router.post("/admin/tags/{tag_id}/deprecate",response_model=TagResponse,summary="Trusted pilot only: production authentication/RBAC is required")
async def deprecate_tag(tag_id:UUID,payload:TagDeprecateRequest,settings:Settings=Depends(get_settings)):
    async with async_session_factory() as session:return await ManagedTagService(session).deprecate(tag_id,payload.replacement_tag_id,payload.expected_updated_at,settings.content_bank_dev_actor_id)

@router.get("/admin/tags/{tag_id}/usage",summary="Trusted pilot only: production authentication/RBAC is required")
async def tag_usage(tag_id:UUID):
    async with async_session_factory() as session:return await ManagedTagService(session).usage(tag_id)

@router.post("/task-versions/check-duplicates",response_model=DuplicateCheckResponse)
async def check_duplicates(payload: DuplicateCheckRequest) -> object:
    async with async_session_factory() as session:
        return await DuplicateCheckService(SQLAlchemyContentBankRepository(session)).check(
            DuplicateQuery(payload.statement,payload.primary_skill_id,payload.final_answer,payload.exclude_task_id,payload.limit))

def _create_command(payload) -> CreateTaskCommand:
    c=payload.initial_version
    return CreateTaskCommand(payload.subject_id,payload.grade_id,payload.topic_id,payload.subtopic_id,VersionContentInput(c.title,c.statement,c.task_type,c.answer_format,c.difficulty,c.source,tuple(SkillLinkInput(x.skill_id,x.weight,x.is_primary) for x in c.skills)), payload.folder_id, tuple(getattr(payload,"tag_ids",())))

@router.put("/task-versions/{version_id}/tags",response_model=VersionTagsResponse)
async def replace_version_tags(version_id:UUID,payload:VersionTagsPutRequest,settings:Settings=Depends(get_settings)):
    async with async_session_factory() as session:
        return await ManagedTagService(session).replace_version_tags(version_id,payload.tag_ids,payload.expected_updated_at,settings.content_bank_dev_actor_id)

@router.put("/task-versions/{task_version_id}/methodology", response_model=MethodologyResponse)
async def put_methodology(task_version_id: UUID, payload: MethodologyPutRequest, settings: Settings = Depends(get_settings)) -> object:
    expected = payload.expected_solution
    rubric = payload.rubric
    command = SaveMethodologyCommand(
        task_version_id,
        ExpectedSolutionInput(expected.solution_text, expected.final_answer, tuple(expected.solution_steps)) if expected else None,
        RubricInput(rubric.grading_mode, rubric.notes, tuple(RubricItemInput(x.criterion, x.max_points, x.required, x.common_failure) for x in rubric.items)) if rubric else None,
        tuple(AcceptedAnswerInput(x.answer_value, x.tolerance, x.unit, x.normalization_rule, x.value_kind, x.canonical_text, x.canonical_decimal, tuple(x.option_keys), x.absolute_tolerance, x.relative_tolerance, x.unit_code, x.normalization_policy_code, x.normalization_policy_version) for x in payload.accepted_answers),
        tuple(TypicalErrorInput(x.skill_id, x.code, x.title, x.description, x.severity, x.remediation_hint, x.detection_hint) for x in payload.typical_errors),
        tuple(HintInput(x.level, x.hint_text) for x in payload.hints),
        tuple(ChoiceOptionInput(x.option_key, x.content, x.order_index) for x in payload.choice_options),
        ChoiceScoringPolicyInput(payload.choice_scoring_policy.mode, payload.choice_scoring_policy.policy_version, tuple(ChoiceOptionRuleInput(x.option_key, x.role, x.weight) for x in payload.choice_scoring_policy.option_rules)) if payload.choice_scoring_policy else None,
    )
    return await SaveMethodologyService(SQLAlchemyUnitOfWork(async_session_factory)).save(command, ActorContext(settings.content_bank_dev_actor_id))


@router.get("/tasks", response_model=TaskListPageResponse)
async def list_tasks(
    subject_id: UUID | None = None, grade_id: UUID | None = None,
    topic_id: UUID | None = None, subtopic_id: UUID | None = None,
    skill_id: UUID | None = None,
    task_type: Literal["test", "calculation", "problem", "open_question", "essay"] | None = None,
    difficulty_min: Annotated[int | None, Query(ge=1, le=100)] = None,
    difficulty_max: Annotated[int | None, Query(ge=1, le=100)] = None,
    status: Literal["draft", "review", "approved", "archived"] | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    sort_by: Literal["created_at", "updated_at", "title", "difficulty", "status", "version_no", "relevance"] | None = None,
    sort_order: Literal["asc", "desc"] = "desc",
    q: str | None = None,
    folder_id: UUID | None = None, folder_scope: Literal["direct", "subtree"] | None = None,
    tag_id: Annotated[list[UUID] | None, Query()] = None,
) -> object:
    query = TaskListQuery(subject_id, grade_id, topic_id, subtopic_id, skill_id, task_type, difficulty_min, difficulty_max, status, offset, limit, sort_by, sort_order, q, folder_id, folder_scope, False, tuple(tag_id or ()))
    async with async_session_factory() as session:
        await ManagedTagService(session).validate_filter(query.tag_ids)
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
    command = CreateTaskCommand(payload.subject_id, payload.grade_id, payload.topic_id, payload.subtopic_id, VersionContentInput(content.title, content.statement, content.task_type, content.answer_format, content.difficulty, content.source, tuple(SkillLinkInput(x.skill_id, x.weight, x.is_primary) for x in content.skills)), payload.folder_id, tuple(payload.tag_ids))
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


@router.get("/subjects/{subject_id}/folders/tree", response_model=FolderTreeResponse)
async def folder_tree(subject_id: UUID):
    async with async_session_factory() as session: return await GetFolderTreeService(SQLAlchemyContentBankRepository(session)).get(subject_id)
@router.post("/subjects/{subject_id}/folders", response_model=FolderSummaryResponse, status_code=201)
async def create_folder(subject_id: UUID,payload:FolderCreateRequest,settings:Settings=Depends(get_settings)):
    return await FolderService(SQLAlchemyUnitOfWork(async_session_factory)).create(CreateFolderCommand(subject_id,payload.parent_id,payload.name,settings.content_bank_dev_actor_id))
@router.patch("/folders/{folder_id}",response_model=FolderSummaryResponse)
async def rename_folder(folder_id:UUID,payload:FolderRenameRequest,settings:Settings=Depends(get_settings)):
    return await FolderService(SQLAlchemyUnitOfWork(async_session_factory)).rename(RenameFolderCommand(folder_id,payload.name,payload.expected_updated_at,settings.content_bank_dev_actor_id))
@router.post("/folders/{folder_id}/move",response_model=FolderSummaryResponse)
async def move_folder(folder_id:UUID,payload:FolderMoveRequest,settings:Settings=Depends(get_settings)):
    return await FolderService(SQLAlchemyUnitOfWork(async_session_factory)).move(MoveFolderCommand(folder_id,payload.parent_id,payload.expected_updated_at,settings.content_bank_dev_actor_id))
@router.delete("/folders/{folder_id}",status_code=204)
async def delete_folder(folder_id:UUID,expected_updated_at:datetime,settings:Settings=Depends(get_settings)):
    await FolderService(SQLAlchemyUnitOfWork(async_session_factory)).delete(DeleteFolderCommand(folder_id,expected_updated_at,settings.content_bank_dev_actor_id))
@router.put("/tasks/{task_id}/location",response_model=TaskLocationResponse)
async def move_task(task_id:UUID,payload:TaskLocationRequest,settings:Settings=Depends(get_settings)):
    return await FolderService(SQLAlchemyUnitOfWork(async_session_factory)).move_task(MoveTaskCommand(task_id,payload.folder_id,payload.expected_folder_id,settings.content_bank_dev_actor_id))

async def _contents(subject_id:UUID,folder_id:UUID|None,query:TaskListQuery):
    # The path owns subject and location; query parameters can only filter tasks.
    query=ListTasksService.normalize_query(__import__('dataclasses').replace(query,subject_id=subject_id,folder_id=None,folder_scope=None,root_only=False))
    async with async_session_factory() as session:
        await ManagedTagService(session).validate_filter(query.tag_ids)
        repo=SQLAlchemyContentBankRepository(session); result=await repo.get_level_contents(subject_id,folder_id,query)
        if result is None:
            from app.application.folders import FolderDomainError
            if folder_id: raise FolderDomainError("folder_not_found","Папка не найдена.",{"folder_id":str(folder_id)},404)
            raise FolderDomainError("subject_not_found","Предмет не найден.",{"subject_id":str(subject_id)},404)
        return result

def _contents_task_query(
    grade_id: UUID | None = None, topic_id: UUID | None = None,
    subtopic_id: UUID | None = None, skill_id: UUID | None = None,
    task_type: Literal["test", "calculation", "problem", "open_question", "essay"] | None = None,
    difficulty_min: Annotated[int | None, Query(ge=1, le=100)] = None,
    difficulty_max: Annotated[int | None, Query(ge=1, le=100)] = None,
    status: Literal["draft", "review", "approved", "archived"] | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    sort_by: Literal["created_at", "updated_at", "title", "difficulty", "status", "version_no", "relevance"] | None = None,
    sort_order: Literal["asc", "desc"] = "desc", q: str | None = None,
    tag_id: Annotated[list[UUID] | None, Query()] = None,
) -> TaskListQuery:
    """Build the safe public subset shared by both direct-contents routes."""
    return TaskListQuery(grade_id=grade_id, topic_id=topic_id, subtopic_id=subtopic_id,
                         skill_id=skill_id, task_type=task_type,
                         difficulty_min=difficulty_min, difficulty_max=difficulty_max,
                         status=status, offset=offset, limit=limit, sort_by=sort_by,
                         sort_order=sort_order, q=q, tag_ids=tuple(tag_id or ()))

@router.get("/subjects/{subject_id}/contents")
async def subject_contents(subject_id:UUID,query:Annotated[TaskListQuery,Depends(_contents_task_query)]):
    return await _contents(subject_id,None,query)
@router.get("/folders/{folder_id}/contents")
async def folder_contents(folder_id:UUID,query:Annotated[TaskListQuery,Depends(_contents_task_query)]):
    async with async_session_factory() as session:
        folder=await SQLAlchemyContentBankRepository(session).get_folder(folder_id)
    if not folder:
        from app.application.folders import FolderDomainError
        raise FolderDomainError("folder_not_found","Папка не найдена.",{"folder_id":str(folder_id)},404)
    return await _contents(folder.subject_id,folder_id,query)
