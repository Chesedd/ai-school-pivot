from uuid import uuid4

from app.application.object_access import ObjectAccessScope, object_access_scope
from app.application.principal import Principal


def principal(user_id, *roles):
    return Principal(user_id, "login", "Name", frozenset(roles), frozenset(), None)


def test_teacher_scope_is_owner_only():
    teacher = uuid4()
    scope = object_access_scope(principal(teacher, "teacher"))
    assert scope.owns(teacher)
    assert not scope.owns(uuid4())
    assert not scope.unrestricted


def test_admin_scope_is_unrestricted_without_capability_substitution():
    admin = uuid4()
    scope = object_access_scope(principal(admin, "admin"))
    assert scope.unrestricted
    assert scope.owns(uuid4())


def test_unknown_legacy_owner_cannot_be_claimed_by_teacher():
    scope = ObjectAccessScope(uuid4())
    assert not scope.owns(uuid4())
    assert ObjectAccessScope(scope.actor_id, unrestricted=True).owns(uuid4())
