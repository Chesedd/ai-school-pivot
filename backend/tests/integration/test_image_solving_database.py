"""PostgreSQL persistence, recovery lease, and concurrent-run coverage."""
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.application.image_solving_contracts import ImageSolvingStatus
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
