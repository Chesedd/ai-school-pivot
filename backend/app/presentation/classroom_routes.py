from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.capabilities import CLASSROOM_USE
from app.application.classroom_access import ClassroomAccessService
from app.application.principal import Principal
from app.db.session import get_session
from app.infrastructure.classroom_repository import SQLAlchemyClassroomAccessRepository
from app.presentation.auth_dependencies import require_capability
from app.presentation.classroom_schemas import (
    TeacherClassPage, TeacherClassResponse, TeacherStudentResponse,
)

router = APIRouter(prefix="/api/classrooms", tags=["classrooms"])
classroom_actor = require_capability(CLASSROOM_USE)


def service(session: AsyncSession = Depends(get_session)):
    return ClassroomAccessService(SQLAlchemyClassroomAccessRepository(session))


def unrestricted(principal: Principal) -> bool:
    return "admin" in principal.roles


@router.get("", response_model=TeacherClassPage)
async def list_classrooms(
    principal: Principal = Depends(classroom_actor),
    access: ClassroomAccessService = Depends(service),
    status: Literal["active", "archived", "all"] = "active",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await access.list_classes(principal.user_id, unrestricted(principal), status, offset, limit)


@router.get("/{class_group_id}", response_model=TeacherClassResponse)
async def get_classroom(class_group_id: UUID, principal: Principal = Depends(classroom_actor),
                        access: ClassroomAccessService = Depends(service)):
    return await access.get_class(class_group_id, principal.user_id, unrestricted(principal))


@router.get("/{class_group_id}/students", response_model=list[TeacherStudentResponse])
async def list_classroom_students(
    class_group_id: UUID,
    principal: Principal = Depends(classroom_actor),
    access: ClassroomAccessService = Depends(service),
    status: Literal["active", "archived", "all"] = "active",
):
    return await access.list_students(
        class_group_id, principal.user_id, unrestricted(principal), status)
