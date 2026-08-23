"""PostgreSQL coverage for immutable input-artifact persistence."""
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.input_artifacts import ArtifactError, ArtifactOwnershipService
from app.infrastructure.input_artifact_repository import SqlAlchemyArtifactRepository

URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("artifact tests require *_test database")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]


@pytest_asyncio.fixture
async def context():
    engine = create_async_engine(URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE input_artifacts CASCADE"))
    yield engine, factory
    await engine.dispose()


async def test_create_read_owner_isolation_and_session_relation(context):
    _, factory = context
    owner, foreign_owner = uuid4(), uuid4()
    async with factory() as db, db.begin():
        service = ArtifactOwnershipService(SqlAlchemyArtifactRepository(db))
        artifact = await service.register_artifact(owner_id=owner, mime_type="application/pdf",
            content_hash_sha256="c" * 64, size_bytes=4096,
            storage_reference=f"object://authoring/{uuid4()}")
    async with factory() as db, db.begin():
        service = ArtifactOwnershipService(SqlAlchemyArtifactRepository(db))
        assert (await service.get_owned_artifact(artifact_id=artifact.id, owner_id=owner)).id == artifact.id
        with pytest.raises(ArtifactError, match="artifact_access_denied"):
            await service.get_owned_artifact(artifact_id=artifact.id, owner_id=foreign_owner)
        session_id = await db.scalar(text("SELECT id FROM authoring_sessions WHERE owner_id=:owner LIMIT 1"), {"owner": owner})
        if session_id is None:
            session_id = uuid4()
            await db.execute(text("""INSERT INTO authoring_sessions
                (id,owner_id,schema_version,policy_version,frozen_request,request_fingerprint,frozen_allowlist)
                VALUES (:id,:owner,'legacy','legacy','{}',:hash,'{}')"""),
                {"id": session_id, "owner": owner, "hash": "d" * 64})
        await db.execute(text("UPDATE authoring_sessions SET input_artifact_id=:artifact WHERE id=:session"),
            {"artifact": artifact.id, "session": session_id})
        assert await db.scalar(text("SELECT input_artifact_id FROM authoring_sessions WHERE id=:id"), {"id": session_id}) == artifact.id


async def test_database_rejects_artifact_update_and_delete(context):
    _, factory = context
    async with factory() as db, db.begin():
        artifact = await ArtifactOwnershipService(SqlAlchemyArtifactRepository(db)).register_artifact(
            owner_id=uuid4(), mime_type="image/webp", content_hash_sha256="e" * 64,
            size_bytes=12, storage_reference=f"object://authoring/{uuid4()}")
    async with factory() as db:
        with pytest.raises(Exception, match="input artifacts are immutable"):
            await db.execute(text("UPDATE input_artifacts SET content_hash_sha256=:hash WHERE id=:id"),
                {"hash": "f" * 64, "id": artifact.id})
    async with factory() as db:
        with pytest.raises(Exception, match="input artifacts are immutable"):
            await db.execute(text("DELETE FROM input_artifacts WHERE id=:id"), {"id": artifact.id})
