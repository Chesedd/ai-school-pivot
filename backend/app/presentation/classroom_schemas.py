from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TeacherClassResponse(BaseModel):
    id: UUID
    name: str
    grade_id: UUID | None
    grade_number: int | None
    grade_name: str | None
    archived_at: datetime | None
    active_student_count: int


class TeacherClassPage(BaseModel):
    items: list[TeacherClassResponse]
    total: int
    offset: int
    limit: int


class TeacherStudentResponse(BaseModel):
    id: UUID
    display_name: str
    external_ref: str | None
    archived_at: datetime | None

