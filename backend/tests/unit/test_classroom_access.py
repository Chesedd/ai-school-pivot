from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.application.classroom_access import (
    ClassroomAccessError, ClassroomAccessService, TeacherClassSummary, TeacherStudentSummary,
)


class Repository:
    def __init__(self):
        self.group = TeacherClassSummary(uuid4(), "7А", uuid4(), 7, "7 класс", None, 2)
        self.calls = []
        self.allowed = True

    async def list_accessible_classes(self, actor, unrestricted, status, offset, limit):
        self.calls.append((actor, unrestricted, status, offset, limit))
        return {"items": [self.group], "total": 1, "offset": offset, "limit": limit}

    async def get_accessible_class(self, class_id, actor, unrestricted):
        self.calls.append((class_id, actor, unrestricted))
        return self.group if self.allowed else None

    async def list_accessible_students(self, class_id, actor, unrestricted, status):
        self.calls.append((class_id, actor, unrestricted, status))
        return [TeacherStudentSummary(uuid4(), "Ученик", "S-1", None)] if self.allowed else None


async def test_teacher_list_forwards_membership_scope_status_and_pagination():
    repository, actor = Repository(), uuid4()
    result = await ClassroomAccessService(repository).list_classes(actor, False, "archived", 20, 10)
    assert [item.name for item in result["items"]] == ["7А"]
    assert repository.calls == [(actor, False, "archived", 20, 10)]


async def test_admin_list_forwards_unrestricted_override():
    repository, actor = Repository(), uuid4()
    await ClassroomAccessService(repository).list_classes(actor, True, "all", 0, 100)
    assert repository.calls == [(actor, True, "all", 0, 100)]


async def test_assigned_class_and_roster_are_readable():
    repository, actor = Repository(), uuid4()
    service = ClassroomAccessService(repository)
    assert (await service.get_class(repository.group.id, actor, False)).name == "7А"
    assert (await service.list_students(repository.group.id, actor, False, "active"))[0].display_name == "Ученик"


async def test_unassigned_class_and_parent_roster_are_non_enumerating():
    repository, actor = Repository(), uuid4()
    repository.allowed = False
    service = ClassroomAccessService(repository)
    with pytest.raises(ClassroomAccessError) as detail:
        await service.get_class(repository.group.id, actor, False)
    assert (detail.value.code, detail.value.status) == ("classroom_not_found", 404)
    with pytest.raises(ClassroomAccessError):
        await service.list_students(repository.group.id, actor, False, "all")
