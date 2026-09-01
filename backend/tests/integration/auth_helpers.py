"""Authenticated identities for legacy domain-level integration tests.

These helpers replace only the canonical session-to-Principal dependency.  The
production capability and object-scope dependencies remain in the request path.
"""
from uuid import UUID

from app.application.capabilities import capabilities_for_roles
from app.application.principal import Principal
from app.presentation.auth_dependencies import require_principal


def principal(user_id: UUID, role: str, *, student_id: UUID | None = None) -> Principal:
    roles = frozenset({role})
    return Principal(user_id, f"{role}-{user_id}", role.title(), roles,
                     capabilities_for_roles(roles), student_id)


def teacher_principal(user_id: UUID) -> Principal:
    return principal(user_id, "teacher")


def admin_principal(user_id: UUID) -> Principal:
    return principal(user_id, "admin")


def student_principal(user_id: UUID, student_id: UUID) -> Principal:
    assert user_id != student_id
    return principal(user_id, "student", student_id=student_id)


def override_principal(app, value: Principal) -> None:
    app.dependency_overrides[require_principal] = lambda: value


def clear_principal_override(app) -> None:
    app.dependency_overrides.pop(require_principal, None)
