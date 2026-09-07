"""Membership-scoped, read-only Classroom application boundary."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

ClassroomStatus = Literal["active", "archived", "all"]


@dataclass(frozen=True)
class TeacherClassSummary:
    id: UUID
    name: str
    grade_id: UUID | None
    grade_number: int | None
    grade_name: str | None
    archived_at: datetime | None
    active_student_count: int


TeacherClassDetail = TeacherClassSummary


@dataclass(frozen=True)
class TeacherStudentSummary:
    id: UUID
    display_name: str
    external_ref: str | None
    archived_at: datetime | None


class ClassroomAccessError(Exception):
    def __init__(self, code: str = "classroom_not_found", status: int = 404):
        self.code = code
        self.status = status
        super().__init__("Класс не найден или больше вам не назначен.")


class ClassroomAccessRepository(Protocol):
    async def list_accessible_classes(self, actor_id: UUID, unrestricted: bool,
                                      status: ClassroomStatus, offset: int, limit: int): ...
    async def get_accessible_class(self, class_group_id: UUID, actor_id: UUID,
                                   unrestricted: bool) -> TeacherClassDetail | None: ...
    async def list_accessible_students(self, class_group_id: UUID, actor_id: UUID,
                                       unrestricted: bool, status: ClassroomStatus): ...
    async def can_access_class(self, class_group_id: UUID, actor_id: UUID, unrestricted: bool,
                               *, require_active: bool = False,
                               require_configured: bool = False, lock: bool = False) -> bool: ...
    async def accessible_class_ids(self, actor_id: UUID, unrestricted: bool) -> tuple[UUID, ...]: ...
    async def list_assignment_eligible_classes(self, actor_id: UUID, unrestricted: bool,
                                               offset: int, limit: int): ...


class ClassroomAccessService:
    def __init__(self, repository: ClassroomAccessRepository):
        self.repository = repository

    async def list_classes(self, actor_id: UUID, unrestricted: bool, status: ClassroomStatus,
                           offset: int, limit: int):
        return await self.repository.list_accessible_classes(
            actor_id, unrestricted, status, offset, limit)

    async def get_class(self, class_group_id: UUID, actor_id: UUID, unrestricted: bool):
        value = await self.repository.get_accessible_class(class_group_id, actor_id, unrestricted)
        if value is None:
            raise ClassroomAccessError()
        return value

    async def list_students(self, class_group_id: UUID, actor_id: UUID, unrestricted: bool,
                            status: ClassroomStatus):
        # The repository applies the parent membership predicate in the same query.
        value = await self.repository.list_accessible_students(
            class_group_id, actor_id, unrestricted, status)
        if value is None:
            raise ClassroomAccessError()
        return value
