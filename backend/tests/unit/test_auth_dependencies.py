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
