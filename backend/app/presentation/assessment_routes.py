"""Thin Assessment Core HTTP routes."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.application.assessments import (AddAssessmentItemCommand, AssessmentService,
    ChangeAssessmentItemPointsCommand, CreateAssessmentCommand,
    PublishAssessmentCommand, ReorderAssessmentItemsCommand, UpdateAssessmentCommand)
from app.application.content_bank import ActorContext
from app.config import Settings, get_settings
from app.db.session import async_session_factory
from app.infrastructure.assessment_repository import SQLAlchemyAssessmentUnitOfWork
from app.presentation.assessment_schemas import (AssessmentCreateRequest, AssessmentItemCreateRequest,
    AssessmentItemOrderRequest, AssessmentItemPatchRequest, AssessmentItemResponse,
    AssessmentClassGroupPage, AssessmentListPage, AssessmentPatchRequest, AssessmentResponse,
    TeacherAssignmentPage, VariantCreateRequest, VariantResponse)
from app.presentation.assessment_schemas import (AssignmentResponse, EmptyRequest,
    PublicationResponse, PublishAssessmentRequest)

router = APIRouter(prefix="/api/assessment-core")


def service() -> AssessmentService:
    return AssessmentService(SQLAlchemyAssessmentUnitOfWork(async_session_factory))


def actor(settings: Settings = Depends(get_settings)) -> ActorContext:
    return ActorContext(settings.content_bank_dev_actor_id)

@router.get("/class-groups", response_model=AssessmentClassGroupPage)
async def list_class_groups(context: Annotated[ActorContext, Depends(actor)],
        offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return await service().list_class_groups(offset, limit, context)

@router.get("/assessments/{assessment_id}/assignments", response_model=TeacherAssignmentPage)
async def list_assessment_assignments(assessment_id: UUID,
        context: Annotated[ActorContext, Depends(actor)], offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return await service().list_assignments(assessment_id, offset, limit, context)


def assessment_view(row):
    variants = []
    for variant in row.variants:
        items = list(variant.items)
        variants.append({"id": variant.id, "name": variant.name, "position": variant.position,
                         "items": items, "total_points": sum((item.points for item in items), start=0)})
    return {"id": row.id, "title": row.title, "description": row.description, "status": row.status,
            "variants": variants, "created_at": row.created_at, "updated_at": row.updated_at,
            "published_at": row.published_at, "published_by": row.published_by}


@router.get("/assessments", response_model=AssessmentListPage)
async def list_assessments(context: Annotated[ActorContext, Depends(actor)], status: Literal["draft", "published"] | None = None, offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    result = await service().list(status, offset, limit, context)
    result["items"] = [{"id": x.id, "title": x.title, "description": x.description, "status": x.status,
                        "variant_count": len(x.variants), "created_at": x.created_at, "updated_at": x.updated_at,
                        "published_at": x.published_at, "published_by": x.published_by} for x in result["items"]]
    return result


@router.post("/assessments", response_model=AssessmentResponse, status_code=201)
async def create_assessment(payload: AssessmentCreateRequest, response: Response, context: Annotated[ActorContext, Depends(actor)]):
    result = await service().create(CreateAssessmentCommand(payload.title, payload.description), context)
    response.headers["Location"] = f"/api/assessment-core/assessments/{result.id}"
    return assessment_view(result)


@router.get("/assessments/{assessment_id}", response_model=AssessmentResponse)
async def get_assessment(assessment_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
    return assessment_view(await service().get(assessment_id, context))


@router.patch("/assessments/{assessment_id}", response_model=AssessmentResponse)
async def patch_assessment(assessment_id: UUID, payload: AssessmentPatchRequest, context: Annotated[ActorContext, Depends(actor)]):
    values = payload.model_dump(include=payload.model_fields_set - {"expected_updated_at"})
    return assessment_view(await service().update(UpdateAssessmentCommand(assessment_id, payload.expected_updated_at, values), context))


@router.post("/assessments/{assessment_id}/variants", response_model=VariantResponse, status_code=201)
async def create_variant(assessment_id: UUID, payload: VariantCreateRequest, response: Response, context: Annotated[ActorContext, Depends(actor)]):
    result = await service().create_variant(assessment_id, payload.name, context)
    response.headers["Location"] = f"/api/assessment-core/assessments/{assessment_id}/variants/{result.id}"
    return result


@router.delete("/assessments/{assessment_id}/variants/{variant_id}", status_code=204)
async def delete_variant(assessment_id: UUID, variant_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
    await service().delete_variant(assessment_id, variant_id, context)
    return Response(status_code=204)


@router.post("/assessments/{assessment_id}/variants/{variant_id}/items",
             response_model=AssessmentItemResponse, status_code=201)
async def add_item(assessment_id: UUID, variant_id: UUID, payload: AssessmentItemCreateRequest,
                   response: Response, context: Annotated[ActorContext, Depends(actor)]):
    result = await service().add_item(AddAssessmentItemCommand(
        assessment_id, variant_id, payload.task_version_id, payload.points), context)
    response.headers["Location"] = (
        f"/api/assessment-core/assessments/{assessment_id}/variants/{variant_id}/items/{result.id}")
    return result


@router.delete("/assessments/{assessment_id}/variants/{variant_id}/items/{item_id}", status_code=204)
async def delete_item(assessment_id: UUID, variant_id: UUID, item_id: UUID,
                      context: Annotated[ActorContext, Depends(actor)]):
    await service().delete_item(assessment_id, variant_id, item_id, context)
    return Response(status_code=204)


@router.put("/assessments/{assessment_id}/variants/{variant_id}/item-order", response_model=VariantResponse)
async def reorder_items(assessment_id: UUID, variant_id: UUID, payload: AssessmentItemOrderRequest,
                        context: Annotated[ActorContext, Depends(actor)]):
    return await service().reorder_items(ReorderAssessmentItemsCommand(
        assessment_id, variant_id, tuple(payload.item_ids), payload.expected_updated_at), context)


@router.patch("/assessments/{assessment_id}/variants/{variant_id}/items/{item_id}",
              response_model=AssessmentItemResponse)
async def patch_item(assessment_id: UUID, variant_id: UUID, item_id: UUID,
                     payload: AssessmentItemPatchRequest,
                     context: Annotated[ActorContext, Depends(actor)]):
    return await service().change_item_points(ChangeAssessmentItemPointsCommand(
        assessment_id, variant_id, item_id, payload.points, payload.expected_updated_at), context)


@router.post("/assessments/{assessment_id}/publish-and-assign", response_model=PublicationResponse, status_code=201)
async def publish_and_assign(assessment_id: UUID, payload: PublishAssessmentRequest, response: Response,
                             context: Annotated[ActorContext, Depends(actor)]):
    result = await service().publish_and_assign(PublishAssessmentCommand(
        assessment_id, payload.class_group_id, payload.start_at, payload.due_at, payload.max_attempts), context)
    response.headers["Location"] = f"/api/assessment-core/assignments/{result.assignment.id}"
    return {"assessment": assessment_view(result.assessment), "assignment": result.assignment}


@router.get("/assignments/{assignment_id}", response_model=AssignmentResponse)
async def get_assignment(assignment_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
    return await service().get_assignment(assignment_id, context)


@router.post("/assignments/{assignment_id}/close", response_model=AssignmentResponse)
async def close_assignment(assignment_id: UUID, payload: EmptyRequest,
                           context: Annotated[ActorContext, Depends(actor)]):
    return await service().close_assignment(assignment_id, context)
