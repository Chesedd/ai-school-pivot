"""PostgreSQL persistence, recovery lease, and concurrent-run coverage."""
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from decimal import Decimal
from app.application.image_solving_contracts import (
    ExtractionResultV1, ImageSolvingStatus, SolutionResultV1, ValidationResultV1,
)
from app.infrastructure.image_solving_repository import SqlAlchemyImageSolvingRepository

URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("image solving tests require *_test database")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]

@pytest_asyncio.fixture
async def context():
    engine=create_async_engine(URL); factory=async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection: await connection.execute(text("TRUNCATE image_solving_sessions, input_artifacts CASCADE"))
    yield engine,factory
    await engine.dispose()

async def create_artifact(factory, owner):
    artifact=uuid4()
    async with factory() as db, db.begin():
        await db.execute(text("INSERT INTO input_artifacts(id,owner_id,mime_type,content_hash_sha256,size_bytes,storage_reference) VALUES(:id,:owner,'image/png',:hash,4,:ref)"), {"id":artifact,"owner":owner,"hash":"a"*64,"ref":f"object://{artifact}"})
    return artifact

async def test_session_persistence_and_concurrent_claim(context):
    _,factory=context; owner=uuid4(); artifact=await create_artifact(factory,owner)
    async with factory() as db:
        state=await SqlAlchemyImageSolvingRepository(db).create(owner,artifact)
    async with factory() as first, factory() as second:
        one=SqlAlchemyImageSolvingRepository(first); two=SqlAlchemyImageSolvingRepository(second)
        assert await one.claim(state.session_id,ImageSolvingStatus.CREATED,ImageSolvingStatus.EXTRACTING)
        assert not await two.claim(state.session_id,ImageSolvingStatus.CREATED,ImageSolvingStatus.EXTRACTING)
        assert (await two.get(state.session_id)).input_artifact_id == artifact

async def test_abandoned_running_lease_is_recovered(context):
    _,factory=context; owner=uuid4(); artifact=await create_artifact(factory,owner)
    async with factory() as db:
        repo=SqlAlchemyImageSolvingRepository(db); state=await repo.create(owner,artifact)
        await db.execute(text("UPDATE image_solving_sessions SET status='extracting',updated_at=:old WHERE id=:id"), {"old":datetime.now(UTC)-timedelta(minutes=6),"id":state.session_id}); await db.commit()
        assert await repo.claim(state.session_id,ImageSolvingStatus.CREATED,ImageSolvingStatus.EXTRACTING)


async def test_checkpoint_jsonb_roundtrip_for_every_stage(context):
    _, factory = context; owner = uuid4(); artifact = await create_artifact(factory, owner)
    extraction = ExtractionResultV1(extracted_text="7 · (X - 3) = 21",
        structured_statement="Решить уравнение: 7 · (X - 3) = 21. Найти X.",
        detected_task_type="solve_linear_equation", detected_answer_format="integer",
        choices=("X = 6", "X = 0", "X = 3", "X = 24"),
        extraction_confidence=Decimal("0.95"), ocr_issues=())
    solution = SolutionResultV1(status="solved",
        reasoning_summary="Divide by 7, then add 3.", final_answer="X = 6",
        confidence=Decimal("0.97"))
    validation = ValidationResultV1(validation_status="validated",
        confidence=Decimal("0.95"), findings=(), requires_human_review=False,
        extraction_confidence_check=True, OCR_quality_check=True,
        solver_status_check=True, answer_consistency_check=True)
    async with factory() as db:
        repo = SqlAlchemyImageSolvingRepository(db)
        session = await repo.create(owner, artifact)
        state = await repo.save_checkpoint(session.session_id, "extraction", extraction,
            ImageSolvingStatus.EXTRACTED)
        assert state.lifecycle_status is ImageSolvingStatus.EXTRACTED
        assert state.extraction_checkpoint == extraction
        state = await repo.save_checkpoint(session.session_id, "solver", solution,
            ImageSolvingStatus.SOLVED)
        assert state.lifecycle_status is ImageSolvingStatus.SOLVED
        assert state.solver_checkpoint == solution
        state = await repo.save_checkpoint(session.session_id, "validation", validation,
            ImageSolvingStatus.VALIDATED)
        assert state.lifecycle_status is ImageSolvingStatus.VALIDATED
        assert state.validation_checkpoint == validation
