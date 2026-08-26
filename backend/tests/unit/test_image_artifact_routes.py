"""HTTP regressions for the image artifact multipart boundary."""
from datetime import UTC, datetime
import os
from uuid import uuid4

import httpx
import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unit:unit@localhost/unit")
os.environ.setdefault("CONTENT_BANK_DEV_ACTOR_ID", "00000000-0000-4000-8000-000000000001")
os.environ.setdefault("ASSESSMENT_DEV_STUDENT_ID", "00000000-0000-4000-8000-000000000002")

from app.application.input_artifacts import ArtifactUploadService, InputArtifactRecord
from app.main import app
from app.presentation.image_artifact_routes import service as artifact_service
from app.presentation.image_solving_routes import image_solving_service
from app.presentation.image_solving_schemas import ImageSolvingSessionResponse

PNG = b"\x89PNG\r\n\x1a\nvalid-small-png"


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

    assert created.status_code == 201
    assert created.json()["session_id"]
    assert created.json()["artifact_id"] == artifact_id
    assert created.json()["status"] == "created"
    assert malformed.status_code == 422
    assert missing.status_code == 422
    assert unexpected.status_code == 422
