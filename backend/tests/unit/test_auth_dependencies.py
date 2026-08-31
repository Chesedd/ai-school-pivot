from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.application.principal import Principal
from app.presentation.auth_dependencies import require_capability, require_principal


def principal(capabilities=frozenset()):
    return Principal(uuid4(), "login", "Name", frozenset(), capabilities, None)


def test_capability_dependency_anonymous_forbidden_and_allowed():
    app = FastAPI()

    @app.get("/protected")
    def protected(value=Depends(require_capability("content.create"))):
        return {"user_id": str(value.user_id)}

    client = TestClient(app)
    assert client.get("/protected").status_code == 401

    app.dependency_overrides[require_principal] = lambda: principal()
    assert client.get("/protected").status_code == 403

    app.dependency_overrides[require_principal] = lambda: principal(frozenset({"content.create"}))
    assert client.get("/protected").status_code == 200


def test_student_identity_requires_current_link_and_uses_domain_id():
    from app.presentation.auth_dependencies import require_student_identity

    user_id = uuid4()
    student_id = uuid4()
    assert user_id != student_id
    linked = Principal(user_id, "student", "Student", frozenset(), frozenset(), student_id)
    assert require_student_identity(linked) == student_id

    unlinked = Principal(user_id, "student", "Student", frozenset({"student"}), frozenset(), None)
    try:
        require_student_identity(unlinked)
    except Exception as exc:
        assert exc.status_code == 403
        assert exc.detail == "student_identity_required"
    else:
        raise AssertionError("an unlinked account must not receive a Student identity")


def test_student_identity_reflects_link_removal_on_next_request():
    from app.presentation.auth_dependencies import require_student_identity

    user_id, student_id = uuid4(), uuid4()
    assert require_student_identity(principal := Principal(
        user_id, "student", "Student", frozenset(), frozenset(), student_id
    )) == student_id
    principal = Principal(user_id, "student", "Student", frozenset(), frozenset(), None)
    try:
        require_student_identity(principal)
    except Exception as exc:
        assert exc.status_code == 403
        assert exc.detail == "student_identity_required"
    else:
        raise AssertionError("removed links must be observed without a session fallback")
