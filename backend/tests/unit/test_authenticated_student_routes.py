"""HTTP boundary proofs for the authenticated Assessment Student identity."""
import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
os.environ.setdefault("CONTENT_BANK_DEV_ACTOR_ID", "00000000-0000-4000-8000-000000000001")

from fastapi.testclient import TestClient

from app.application.principal import Principal
from app.main import app
from app.presentation.auth_dependencies import require_principal
import app.presentation.student_assessment_routes as routes


class RecordingService:
    def __init__(self):
        self.student_ids = []

    async def list_assignments(self, student_id, offset, limit):
        self.student_ids.append(student_id)
        return {"items": [], "total": 0, "offset": offset, "limit": limit}


def principal(user_id, student_id):
    return Principal(user_id, "account", "Student", frozenset(), frozenset(), student_id)


def test_assignment_list_uses_link_not_user_or_spoofed_identity(monkeypatch):
    user_id, student_id, spoofed_id = uuid4(), uuid4(), uuid4()
    assert user_id != student_id
    recording = RecordingService()
    monkeypatch.setattr(routes, "service", lambda: recording)
    app.dependency_overrides[require_principal] = lambda: principal(user_id, student_id)
    try:
        response = TestClient(app).get(
            "/api/assessment-core/student/assignments",
            params={"student_id": str(spoofed_id)},
            headers={"X-Student-Id": str(spoofed_id)},
        )
    finally:
        app.dependency_overrides.pop(require_principal, None)
    assert response.status_code == 200
    assert recording.student_ids == [student_id]
    assert user_id not in recording.student_ids
    assert spoofed_id not in recording.student_ids


def test_assignment_list_requires_authentication_and_student_link(monkeypatch):
    monkeypatch.setattr(routes, "service", RecordingService)
    client = TestClient(app)
    assert client.get("/api/assessment-core/student/assignments").status_code == 401

    app.dependency_overrides[require_principal] = lambda: principal(uuid4(), None)
    try:
        response = client.get("/api/assessment-core/student/assignments")
    finally:
        app.dependency_overrides.pop(require_principal, None)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "student_identity_required"
