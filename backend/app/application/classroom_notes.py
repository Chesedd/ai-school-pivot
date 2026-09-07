"""Private, membership-scoped teacher notes application boundary."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

@dataclass(frozen=True)
class NoteRecord:
    id: UUID
    body: str
    teacher_user_id: UUID
    teacher_display_name: str
    class_group_id: UUID
    created_at: datetime
    updated_at: datetime
    student_id: UUID | None = None

class ClassroomNotesError(Exception):
    def __init__(self, code: str, status: int):
        self.code, self.status = code, status
        super().__init__({"note_not_found":"Заметка не найдена.","note_concurrent_conflict":"Эта заметка уже была изменена.","note_body_invalid":"Текст заметки должен содержать от 1 до 12000 символов.","classroom_not_found":"Класс не найден или больше вам не назначен.","classroom_archived":"Нельзя добавлять заметки в архивный класс.","student_not_found":"Ученик не найден в этом классе.","student_archived":"Нельзя добавлять заметки архивному ученику."}.get(code, code))

class NotesRepository(Protocol):
    async def class_state(self, class_id, actor, unrestricted): ...
    async def student_state(self, class_id, student_id, actor, unrestricted, historical=False): ...
    async def list_notes(self, kind, class_id, student_id, actor, unrestricted, offset, limit): ...
    async def get_note(self, kind, class_id, student_id, note_id, actor, unrestricted, delete=False): ...
    async def create_note(self, kind, class_id, student_id, actor, body): ...
    async def update_note(self, kind, note_id, actor, body, expected): ...
    async def delete_note(self, kind, note_id): ...

class ClassroomNotesService:
    def __init__(self, repository: NotesRepository): self.repository = repository
    @staticmethod
    def body(value: str) -> str:
        value = value.strip()
        if not value or len(value) > 12000: raise ClassroomNotesError("note_body_invalid", 422)
        return value
    async def _class(self, class_id, actor, unrestricted, creating=False):
        state = await self.repository.class_state(class_id, actor, unrestricted)
        if state is None: raise ClassroomNotesError("classroom_not_found", 404)
        if creating and state: raise ClassroomNotesError("classroom_archived", 409)
    async def _student(self, class_id, student_id, actor, unrestricted, creating=False):
        await self._class(class_id, actor, unrestricted, creating)
        state = await self.repository.student_state(
            class_id, student_id, actor, unrestricted, historical=unrestricted and not creating)
        if state is None: raise ClassroomNotesError("student_not_found", 404)
        if creating and state: raise ClassroomNotesError("student_archived", 409)
    async def list_class(self, class_id, actor, unrestricted, offset, limit):
        await self._class(class_id, actor, unrestricted)
        return await self.repository.list_notes("class", class_id, None, actor, unrestricted, offset, limit)
    async def list_student(self, class_id, student_id, actor, unrestricted, offset, limit):
        await self._student(class_id, student_id, actor, unrestricted)
        return await self.repository.list_notes("student", class_id, student_id, actor, unrestricted, offset, limit)
    async def create_class(self, class_id, actor, unrestricted, body):
        await self._class(class_id, actor, unrestricted, True)
        return await self.repository.create_note("class", class_id, None, actor, self.body(body))
    async def create_student(self, class_id, student_id, actor, unrestricted, body):
        await self._student(class_id, student_id, actor, unrestricted, True)
        return await self.repository.create_note("student", class_id, student_id, actor, self.body(body))
    async def get(self, kind, class_id, student_id, note_id, actor, unrestricted):
        await (self._student(class_id, student_id, actor, unrestricted) if kind == "student" else self._class(class_id, actor, unrestricted))
        value = await self.repository.get_note(kind, class_id, student_id, note_id, actor, unrestricted)
        if value is None: raise ClassroomNotesError("note_not_found", 404)
        return value
    async def update(self, kind, class_id, student_id, note_id, actor, unrestricted, body, expected):
        # Even admins may edit only their own note; the repository's author predicate is unconditional.
        await (self._student(class_id, student_id, actor, unrestricted) if kind == "student" else self._class(class_id, actor, unrestricted))
        visible = await self.repository.get_note(kind, class_id, student_id, note_id, actor, False)
        if visible is None: raise ClassroomNotesError("note_not_found", 404)
        value = await self.repository.update_note(kind, note_id, actor, self.body(body), expected)
        if value is None: raise ClassroomNotesError("note_concurrent_conflict", 409)
        return value
    async def delete(self, kind, class_id, student_id, note_id, actor, unrestricted):
        await (self._student(class_id, student_id, actor, unrestricted) if kind == "student" else self._class(class_id, actor, unrestricted))
        value = await self.repository.get_note(kind, class_id, student_id, note_id, actor, unrestricted, True)
        if value is None: raise ClassroomNotesError("note_not_found", 404)
        await self.repository.delete_note(kind, note_id)
