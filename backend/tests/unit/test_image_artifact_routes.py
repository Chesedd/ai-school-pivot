"""HTTP regressions for the image artifact multipart boundary."""
from datetime import UTC, datetime
import os
from uuid import uuid4

import httpx
import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unit:unit@localhost/unit")

from app.application.input_artifacts import ArtifactUploadService, InputArtifactRecord
from app.application.principal import Principal
from app.main import app
from app.presentation.auth_dependencies import require_principal
from app.presentation.image_artifact_routes import service as artifact_service
from app.presentation.image_solving_routes import image_solving_service
from app.presentation.image_solving_schemas import ImageSolvingSessionResponse

PNG = b"\x89PNG\r\n\x1a\nvalid-small-png"
USER_A = uuid4()


def user_a():
    return Principal(USER_A, "user-a", "User A", frozenset(), frozenset(), None)


class Repository:
    async def create(self, **metadata):
        return InputArtifactRecord(id=uuid4(), created_at=datetime.now(UTC), **metadata)

    async def commit(self):
        return None


class Storage:
    async def store(self, content, mime_type):
        assert content == PNG and mime_type == "image/png"
        return "test/artifact.png"

    async def delete(self, storage_reference):
        raise AssertionError(f"unexpected storage rollback: {storage_reference}")


class ImageSolvingService:
    async def create(self, payload, owner_id):
        return ImageSolvingSessionResponse(session_id=uuid4(),
            artifact_id=payload.artifact_id, status="created")


@pytest.mark.asyncio
async def test_real_multipart_upload_and_field_cardinality_contract():
    """Use Request.form() and a real service so UploadFile types cannot be mocked away."""
    async def upload_dependency():
        return ArtifactUploadService(Repository(), Storage())

    app.dependency_overrides[artifact_service] = upload_dependency
    app.dependency_overrides[require_principal] = user_a
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url="http://test") as client:
            uploaded = await client.post("/api/image-solving/artifacts",
                files={"file": ("task.png", PNG, "image/png")})
            missing = await client.post("/api/image-solving/artifacts",
                files={"context": (None, "worksheet")})
            duplicate = await client.post("/api/image-solving/artifacts", files=[
                ("file", ("first.png", PNG, "image/png")),
                ("file", ("second.png", PNG, "image/png")),
            ])
            unexpected = await client.post("/api/image-solving/artifacts", files=[
                ("file", ("task.png", PNG, "image/png")),
                ("owner_id", (None, str(uuid4()))),
            ])
    finally:
        app.dependency_overrides.pop(artifact_service, None)
        app.dependency_overrides.pop(require_principal, None)

    assert uploaded.status_code == 201
    assert uploaded.json()["artifact_id"]
    assert missing.status_code == 422
    assert duplicate.status_code == 422
    assert unexpected.status_code == 422


@pytest.mark.asyncio
async def test_uploaded_artifact_uuid_creates_session_through_json_boundary():
    async def upload_dependency():
        return ArtifactUploadService(Repository(), Storage())

    async def image_solving_dependency():
        return ImageSolvingService()

    app.dependency_overrides[artifact_service] = upload_dependency
    app.dependency_overrides[image_solving_service] = image_solving_dependency
    app.dependency_overrides[require_principal] = user_a
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url="http://test") as client:
            uploaded = await client.post("/api/image-solving/artifacts",
                files={"file": ("task.png", PNG, "image/png")})
            assert uploaded.status_code == 201
            artifact_id = uploaded.json()["artifact_id"]

            created = await client.post("/api/image-solving/sessions",
                json={"artifact_id": artifact_id})
            malformed = await client.post("/api/image-solving/sessions",
                json={"artifact_id": "not-a-uuid"})
            missing = await client.post("/api/image-solving/sessions", json={})
            unexpected = await client.post("/api/image-solving/sessions",
                json={"artifact_id": artifact_id, "owner_id": str(uuid4())})
    finally:
        app.dependency_overrides.pop(artifact_service, None)
        app.dependency_overrides.pop(image_solving_service, None)
        app.dependency_overrides.pop(require_principal, None)

    assert created.status_code == 201
    assert created.json()["session_id"]
    assert created.json()["artifact_id"] == artifact_id
    assert created.json()["status"] == "created"
    assert malformed.status_code == 422
    assert missing.status_code == 422
    assert unexpected.status_code == 422


@pytest.mark.asyncio
async def test_image_solving_routes_require_authentication():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
            base_url="http://test") as client:
        upload_response = await client.post("/api/image-solving/artifacts",
            files={"file": ("task.png", PNG, "image/png")})
        session_response = await client.post("/api/image-solving/sessions",
            json={"artifact_id": str(uuid4())})

    assert upload_response.status_code == 401
    assert upload_response.json()["detail"] == "authentication_required"
    assert session_response.status_code == 401
    assert session_response.json()["detail"] == "authentication_required"


@pytest.mark.asyncio
async def test_authenticated_user_is_authoritative_owner_for_upload_and_session():
    captured = []

    class CapturingRepository(Repository):
        async def create(self, **metadata):
            captured.append(("artifact", metadata["owner_id"]))
            return await super().create(**metadata)

    class CapturingImageSolvingService(ImageSolvingService):
        async def create(self, payload, owner_id):
            captured.append(("session", owner_id))
            return await super().create(payload, owner_id)

    app.dependency_overrides[artifact_service] = lambda: ArtifactUploadService(
        CapturingRepository(), Storage())
    app.dependency_overrides[image_solving_service] = CapturingImageSolvingService
    app.dependency_overrides[require_principal] = user_a
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url="http://test") as client:
            uploaded = await client.post("/api/image-solving/artifacts",
                files={"file": ("task.png", PNG, "image/png")})
            created = await client.post("/api/image-solving/sessions", json={
                "artifact_id": uploaded.json()["artifact_id"],
            })
            spoofed = await client.post("/api/image-solving/sessions", json={
                "artifact_id": uploaded.json()["artifact_id"],
                "owner_id": str(uuid4()),
            })
    finally:
        app.dependency_overrides.pop(artifact_service, None)
        app.dependency_overrides.pop(image_solving_service, None)
        app.dependency_overrides.pop(require_principal, None)

    assert uploaded.status_code == 201
    assert created.status_code == 201
    assert spoofed.status_code == 422  # the spoofable field is schema-forbidden
    assert captured == [("artifact", USER_A), ("session", USER_A)]
