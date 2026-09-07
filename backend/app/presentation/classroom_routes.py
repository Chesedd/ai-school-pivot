from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.capabilities import CLASSROOM_USE, CLASSROOM_NOTES_MANAGE
from app.application.classroom_access import ClassroomAccessService
from app.application.classroom_notes import ClassroomNotesService
from app.application.principal import Principal
from app.db.session import get_session
from app.infrastructure.classroom_repository import SQLAlchemyClassroomAccessRepository, SQLAlchemyClassroomNotesRepository
from app.presentation.auth_dependencies import require_capability
from app.presentation.classroom_schemas import (
    TeacherClassPage, TeacherClassResponse, TeacherStudentResponse, NoteCreate, NotePage,
    NoteResponse, NoteUpdate,
)

router = APIRouter(prefix="/api/classrooms", tags=["classrooms"])
classroom_actor = require_capability(CLASSROOM_USE)
notes_actor = require_capability(CLASSROOM_NOTES_MANAGE)


def service(session: AsyncSession = Depends(get_session)):
    return ClassroomAccessService(SQLAlchemyClassroomAccessRepository(session))

def notes_service(session: AsyncSession = Depends(get_session)):
    return ClassroomNotesService(SQLAlchemyClassroomNotesRepository(session))


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

@router.get("/{class_group_id}/students/{student_id}", response_model=TeacherStudentResponse)
async def get_classroom_student(class_group_id: UUID, student_id: UUID,
    principal: Principal = Depends(classroom_actor), access: ClassroomAccessService = Depends(service)):
    return await access.get_student(class_group_id, student_id, principal.user_id, unrestricted(principal))

@router.get("/{class_group_id}/notes", response_model=NotePage)
async def list_class_notes(class_group_id: UUID, principal: Principal = Depends(notes_actor),
    notes: ClassroomNotesService = Depends(notes_service), offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50):
    return await notes.list_class(class_group_id, principal.user_id, unrestricted(principal), offset, limit)

@router.post("/{class_group_id}/notes", response_model=NoteResponse, status_code=201)
async def create_class_note(class_group_id: UUID, payload: NoteCreate, response: Response,
    principal: Principal = Depends(notes_actor), notes: ClassroomNotesService = Depends(notes_service),
    session: AsyncSession = Depends(get_session)):
    value = await notes.create_class(class_group_id, principal.user_id, unrestricted(principal), payload.body)
    await session.commit(); response.headers["Location"] = f"/api/classrooms/{class_group_id}/notes/{value.id}"
    return value

@router.get("/{class_group_id}/notes/{note_id}", response_model=NoteResponse)
async def get_class_note(class_group_id: UUID, note_id: UUID, principal: Principal = Depends(notes_actor), notes: ClassroomNotesService = Depends(notes_service)):
    return await notes.get("class", class_group_id, None, note_id, principal.user_id, unrestricted(principal))

@router.patch("/{class_group_id}/notes/{note_id}", response_model=NoteResponse)
async def update_class_note(class_group_id: UUID, note_id: UUID, payload: NoteUpdate,
    principal: Principal = Depends(notes_actor), notes: ClassroomNotesService = Depends(notes_service), session: AsyncSession = Depends(get_session)):
    value = await notes.update("class", class_group_id, None, note_id, principal.user_id, unrestricted(principal), payload.body, payload.expected_updated_at)
    await session.commit(); return value

@router.delete("/{class_group_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_class_note(class_group_id: UUID, note_id: UUID, principal: Principal = Depends(notes_actor), notes: ClassroomNotesService = Depends(notes_service), session: AsyncSession = Depends(get_session)):
    await notes.delete("class", class_group_id, None, note_id, principal.user_id, unrestricted(principal)); await session.commit()

@router.get("/{class_group_id}/students/{student_id}/notes", response_model=NotePage)
async def list_student_notes(class_group_id: UUID, student_id: UUID, principal: Principal = Depends(notes_actor), notes: ClassroomNotesService = Depends(notes_service), offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50):
    return await notes.list_student(class_group_id, student_id, principal.user_id, unrestricted(principal), offset, limit)

@router.post("/{class_group_id}/students/{student_id}/notes", response_model=NoteResponse, status_code=201)
async def create_student_note(class_group_id: UUID, student_id: UUID, payload: NoteCreate, response: Response, principal: Principal = Depends(notes_actor), notes: ClassroomNotesService = Depends(notes_service), session: AsyncSession = Depends(get_session)):
    value = await notes.create_student(class_group_id, student_id, principal.user_id, unrestricted(principal), payload.body)
    await session.commit(); response.headers["Location"] = f"/api/classrooms/{class_group_id}/students/{student_id}/notes/{value.id}"
    return value

@router.get("/{class_group_id}/students/{student_id}/notes/{note_id}", response_model=NoteResponse)
async def get_student_note(class_group_id: UUID, student_id: UUID, note_id: UUID, principal: Principal = Depends(notes_actor), notes: ClassroomNotesService = Depends(notes_service)):
    return await notes.get("student", class_group_id, student_id, note_id, principal.user_id, unrestricted(principal))

@router.patch("/{class_group_id}/students/{student_id}/notes/{note_id}", response_model=NoteResponse)
async def update_student_note(class_group_id: UUID, student_id: UUID, note_id: UUID, payload: NoteUpdate, principal: Principal = Depends(notes_actor), notes: ClassroomNotesService = Depends(notes_service), session: AsyncSession = Depends(get_session)):
    value = await notes.update("student", class_group_id, student_id, note_id, principal.user_id, unrestricted(principal), payload.body, payload.expected_updated_at)
    await session.commit(); return value

@router.delete("/{class_group_id}/students/{student_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_student_note(class_group_id: UUID, student_id: UUID, note_id: UUID, principal: Principal = Depends(notes_actor), notes: ClassroomNotesService = Depends(notes_service), session: AsyncSession = Depends(get_session)):
    await notes.delete("student", class_group_id, student_id, note_id, principal.user_id, unrestricted(principal)); await session.commit()
