"""End-to-end validation of the image-solving persistence and HTTP flow."""
import hashlib
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("TEST_DATABASE_URL", "")
os.environ.setdefault("DATABASE_URL", URL or "postgresql+asyncpg://unit:unit@localhost/unit")

from app.application.image_solving import ImageSolvingService
from app.application.image_solving_api import ImageSolvingApplicationService
from app.application.image_solving_contracts import (
    ExtractionResultV1, ImageSolvingStatus, SolutionResultV1,
)
from app.application.input_artifacts import ArtifactOwnershipService, ArtifactUploadService
from app.infrastructure.artifact_storage import FilesystemArtifactStorage
from app.infrastructure.image_solving_repository import SqlAlchemyImageSolvingRepository
from app.infrastructure.input_artifact_repository import SqlAlchemyArtifactRepository
from app.main import app
from app.presentation.image_artifact_routes import service as artifact_service
from app.presentation.image_solving_routes import image_solving_service
from tests.integration.auth_helpers import clear_principal_override, override_principal, teacher_principal

if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("image solving tests require *_test database")
pytestmark = [pytest.mark.asyncio, pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]

EXTRACTION = ExtractionResultV1(extracted_text="2 + 2", structured_statement="2 + 2 = ?",
    detected_task_type="calculation", detected_answer_format="number", choices=None,
    extraction_confidence=Decimal(".99"), ocr_issues=(), metadata={"title":"Сложение чисел","subject":"Математика","grade":1,"topic":"Сложение","subtopic":"Натуральные числа","skills":("Складывать числа",),"task_type":"calculation","answer_format":"number","difficulty":1,"tags":()})
SOLUTION = SolutionResultV1(status="solved", reasoning_summary="Add the two values.",
    final_answer="4", confidence=Decimal(".98"))
FILES = (
    ("task.png", "image/png", b"\x89PNG\r\n\x1a\nfixture"),
    ("task.jpg", "image/jpeg", b"\xff\xd8\xfffixture"),
    ("task.webp", "image/webp", b"RIFF\x04\x00\x00\x00WEBPfixture"),
    ("task.pdf", "application/pdf", b"%PDF-1.7\nfixture"),
)


class Extractor:
    def __init__(self): self.calls = []
    async def extract(self, value): self.calls.append(value); return EXTRACTION


class Solver:
    def __init__(self): self.calls = []
    async def solve(self, value): self.calls.append(value); return SOLUTION


class Integrity:
    def __init__(self, storage): self.storage = storage
    async def sha256(self, artifact):
        return hashlib.sha256(await self.storage.read(artifact.storage_reference)).hexdigest()


class TracingRepository(SqlAlchemyImageSolvingRepository):
    def __init__(self, db, transitions):
        super().__init__(db); self.transitions = transitions
    async def claim(self, session_id, expected, running):
        claimed = await super().claim(session_id, expected, running)
        if claimed: self.transitions.append(running)
        return claimed
    async def save_checkpoint(self, session_id, stage, payload, status, **kwargs):
        state = await super().save_checkpoint(session_id, stage, payload, status, **kwargs)
        self.transitions.append(status)
        return state


@pytest_asyncio.fixture
async def context(tmp_path):
    engine = create_async_engine(URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE image_solving_sessions, input_artifacts CASCADE"))
    yield engine, factory, FilesystemArtifactStorage(str(tmp_path))
    app.dependency_overrides.clear()
    await engine.dispose()


async def upload(factory, storage, owner, content=FILES[0][2], mime=FILES[0][1]):
    async with factory() as db:
        return await ArtifactUploadService(SqlAlchemyArtifactRepository(db), storage).upload(
            owner_id=owner, content=content, claimed_mime_type=mime)


@pytest.mark.parametrize(("filename", "mime", "content"), FILES)
async def test_upload_persists_metadata_but_not_raw_binary(context, filename, mime, content):
    engine, factory, storage = context
    artifact = await upload(factory, storage, uuid4(), content, mime)
    async with engine.connect() as connection:
        row = (await connection.execute(text(
            "SELECT mime_type, size_bytes, content_hash_sha256, storage_reference "
            "FROM input_artifacts WHERE id=:id"), {"id": artifact.id})).one()
        columns = {item[0] for item in (await connection.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='input_artifacts'"))).all()}
    assert row.mime_type == mime and row.size_bytes == len(content)
    assert row.content_hash_sha256 == hashlib.sha256(content).hexdigest()
    assert await storage.read(row.storage_reference) == content
    assert not ({"content", "binary", "data", "blob", "raw_bytes"} & columns)


async def test_pipeline_checkpoints_ownership_and_resume_after_extraction(context):
    _, factory, storage = context
    owner = uuid4(); artifact = await upload(factory, storage, owner)
    extractor, solver, transitions = Extractor(), Solver(), [ImageSolvingStatus.CREATED]
    async with factory() as db:
        repo = TracingRepository(db, transitions)
        flow = ImageSolvingService(repo, ArtifactOwnershipService(SqlAlchemyArtifactRepository(db)),
            Integrity(storage), extractor, solver)
        session = await flow.create_session(owner_id=owner, input_artifact_id=artifact.id)
        assert session.owner_id == owner and session.lifecycle_status is ImageSolvingStatus.CREATED
        # This is the durable boundary left by a process crash after extraction.
        assert await repo.claim(session.session_id, ImageSolvingStatus.CREATED, ImageSolvingStatus.EXTRACTING)
        await repo.save_checkpoint(session.session_id, "extraction", EXTRACTION, ImageSolvingStatus.EXTRACTED)

    restarted_extractor, restarted_solver = Extractor(), Solver()
    async with factory() as db:
        repo = TracingRepository(db, transitions)
        restarted = ImageSolvingService(repo, ArtifactOwnershipService(SqlAlchemyArtifactRepository(db)),
            Integrity(storage), restarted_extractor, restarted_solver)
        result = await restarted.resume(session_id=session.session_id, owner_id=owner)
        assert result.lifecycle_status is ImageSolvingStatus.VALIDATED
        assert result.extraction_checkpoint == EXTRACTION and result.validation_checkpoint is not None
        assert restarted_extractor.calls == [] and len(restarted_solver.calls) == 1
        solver_payload = restarted_solver.calls[0].model_dump(mode="json")
        assert not ({"artifact_id", "content_hash", "user_context", "storage_reference",
                     "prompt", "provider_payload", "raw_reasoning"} & solver_payload.keys())
        assert [row.stage for row in await repo.attempts(session.session_id)] == [
            "extraction", "solver", "validation"]
        assert transitions == [ImageSolvingStatus.CREATED, ImageSolvingStatus.EXTRACTING,
            ImageSolvingStatus.EXTRACTED, ImageSolvingStatus.SOLVING,
            ImageSolvingStatus.SOLVED, ImageSolvingStatus.VALIDATED]


async def test_stale_solver_recovery_is_bounded_and_creates_one_checkpoint(context):
    _, factory, storage = context
    owner = uuid4(); artifact = await upload(factory, storage, owner)
    async with factory() as db:
        repo = SqlAlchemyImageSolvingRepository(db)
        session = await repo.create(owner, artifact.id)
        await repo.claim(session.session_id, ImageSolvingStatus.CREATED, ImageSolvingStatus.EXTRACTING)
        await repo.save_checkpoint(session.session_id, "extraction", EXTRACTION, ImageSolvingStatus.EXTRACTED)
        await repo.claim(session.session_id, ImageSolvingStatus.EXTRACTED, ImageSolvingStatus.SOLVING)
        await db.execute(text("UPDATE image_solving_sessions SET updated_at=:old WHERE id=:id"),
            {"old": datetime.now(UTC) - timedelta(minutes=6), "id": session.session_id})
        await db.commit()
    solver = Solver()
    async with factory() as db:
        repo = SqlAlchemyImageSolvingRepository(db)
        flow = ImageSolvingService(repo, ArtifactOwnershipService(SqlAlchemyArtifactRepository(db)),
            Integrity(storage), Extractor(), solver)
        first = await flow.resume(session_id=session.session_id, owner_id=owner)
        second = await flow.resume(session_id=session.session_id, owner_id=owner)
        assert first == second and len(solver.calls) == 1
        assert await db.scalar(text("SELECT count(*) FROM image_solving_checkpoints "
            "WHERE session_id=:id AND stage='solver'"), {"id": session.session_id}) == 1


async def test_complete_http_api_flow_uses_mocked_providers(context):
    _, factory, storage = context
    owner = uuid4()
    extractor, solver = Extractor(), Solver()

    async def upload_dependency():
        async with factory() as db:
            yield ArtifactUploadService(SqlAlchemyArtifactRepository(db), storage)

    async def solving_dependency():
        async with factory() as db:
            repo = SqlAlchemyImageSolvingRepository(db)
            flow = ImageSolvingService(repo, ArtifactOwnershipService(SqlAlchemyArtifactRepository(db)),
                Integrity(storage), extractor, solver)
            yield ImageSolvingApplicationService(flow, repo)

    app.dependency_overrides[artifact_service] = upload_dependency
    app.dependency_overrides[image_solving_service] = solving_dependency
    override_principal(app, teacher_principal(owner))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            uploaded = await client.post("/api/image-solving/artifacts",
                files={"file": (FILES[0][0], FILES[0][2], FILES[0][1])})
            assert uploaded.status_code == 201
            created = await client.post("/api/image-solving/sessions",
                json={"artifact_id": uploaded.json()["artifact_id"]})
            assert created.status_code == 201 and created.json()["status"] == "created"
            session_id = created.json()["session_id"]
            ran = await client.post(f"/api/image-solving/sessions/{session_id}/run")
            state = await client.get(f"/api/image-solving/sessions/{session_id}")
            result = await client.get(f"/api/image-solving/sessions/{session_id}/result")
    finally:
        app.dependency_overrides.pop(artifact_service, None)
        app.dependency_overrides.pop(image_solving_service, None)
        clear_principal_override(app)
    assert ran.status_code == state.status_code == result.status_code == 200
    assert state.json()["status"] == "validated" and result.json()["solution"]["answer"] == "4"
    assert len(extractor.calls) == len(solver.calls) == 1
    serialized = result.text.lower()
    assert not any(secret in serialized for secret in (
        "prompt", "provider_payload", "raw_reasoning", "storage_reference"))
    async with factory() as db:
        assert await db.scalar(text("SELECT owner_id FROM image_solving_sessions WHERE id=:id"),
            {"id": session_id}) == owner
