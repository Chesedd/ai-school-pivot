"""Capability-protected global account administration API."""
from datetime import datetime
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.application.authentication import AuthenticationService
from app.application.principal import Principal
from app.application.user_administration import AdminUserView, UserAdministrationService
from app.db.session import get_session
from app.infrastructure.auth_repository import SQLAlchemyAuthRepository
from app.presentation.auth_dependencies import require_capability, require_trusted_origin

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])
managed = require_capability("users.manage")
unsafe = [Depends(require_trusted_origin)]

def service(session: AsyncSession = Depends(get_session)) -> UserAdministrationService:
    repository = SQLAlchemyAuthRepository(session)
    return UserAdministrationService(repository, AuthenticationService(repository))

class UserResponse(BaseModel):
    user_id: UUID
    login: str
    display_name: str
    is_active: bool
    roles: list[str]
    student_id: UUID | None
    created_at: datetime
    updated_at: datetime
    @classmethod
    def from_view(cls, value: AdminUserView):
        return cls(**{**value.__dict__, "roles": list(value.roles)})

class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    offset: int
    limit: int

class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: Annotated[str, Field(min_length=1, max_length=254)]
    display_name: Annotated[str, Field(min_length=1, max_length=200)]
    password: Annotated[str, Field(min_length=1, max_length=1024)]
    roles: set[str] = Field(default_factory=set)
    student_id: UUID | None = None

class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: Annotated[str, Field(min_length=1, max_length=254)] | None = None
    display_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    is_active: bool | None = None
    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set: raise ValueError("at least one field is required")
        return self

class RolesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: set[str]

class StudentLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: UUID

class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: Annotated[str, Field(min_length=1, max_length=1024)]

@router.get("", response_model=UserListResponse)
async def list_users(offset: Annotated[int, Field(ge=0)] = 0, limit: Annotated[int, Field(ge=1, le=100)] = 50, _: Principal = Depends(managed), svc: UserAdministrationService = Depends(service)):
    values, total = await svc.list(offset=offset, limit=limit)
    return UserListResponse(items=[UserResponse.from_view(x) for x in values], total=total, offset=offset, limit=limit)

@router.post("", response_model=UserResponse, status_code=201, dependencies=unsafe)
async def create_user(body: CreateUserRequest, _: Principal = Depends(managed), svc: UserAdministrationService = Depends(service), session: AsyncSession = Depends(get_session)):
    value = await svc.create(**body.model_dump()); await session.commit(); return UserResponse.from_view(value)

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: UUID, _: Principal = Depends(managed), svc: UserAdministrationService = Depends(service)):
    return UserResponse.from_view(await svc.get(user_id))

@router.patch("/{user_id}", response_model=UserResponse, dependencies=unsafe)
async def update_user(user_id: UUID, body: UpdateUserRequest, _: Principal = Depends(managed), svc: UserAdministrationService = Depends(service), session: AsyncSession = Depends(get_session)):
    value = await svc.update(user_id, **body.model_dump()); await session.commit(); return UserResponse.from_view(value)

@router.put("/{user_id}/roles", response_model=UserResponse, dependencies=unsafe)
async def set_roles(user_id: UUID, body: RolesRequest, _: Principal = Depends(managed), svc: UserAdministrationService = Depends(service), session: AsyncSession = Depends(get_session)):
    value = await svc.set_roles(user_id, body.roles); await session.commit(); return UserResponse.from_view(value)

@router.put("/{user_id}/student-link", response_model=UserResponse, dependencies=unsafe)
async def link_student(user_id: UUID, body: StudentLinkRequest, _: Principal = Depends(managed), svc: UserAdministrationService = Depends(service), session: AsyncSession = Depends(get_session)):
    value = await svc.link_student(user_id, body.student_id); await session.commit(); return UserResponse.from_view(value)

@router.delete("/{user_id}/student-link", response_model=UserResponse, dependencies=unsafe)
async def unlink_student(user_id: UUID, _: Principal = Depends(managed), svc: UserAdministrationService = Depends(service), session: AsyncSession = Depends(get_session)):
    value = await svc.unlink_student(user_id); await session.commit(); return UserResponse.from_view(value)

@router.post("/{user_id}/password-reset", status_code=204, dependencies=unsafe)
async def reset_password(user_id: UUID, body: PasswordResetRequest, _: Principal = Depends(managed), svc: UserAdministrationService = Depends(service), session: AsyncSession = Depends(get_session)):
    await svc.reset_password(user_id, body.new_password); await session.commit()
