"""PostgreSQL adapter for the authoring input-artifact boundary."""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.input_artifacts import InputArtifactRecord
from app.infrastructure.authoring_models import InputArtifact


def _record(row: InputArtifact) -> InputArtifactRecord:
    return InputArtifactRecord(id=row.id, owner_id=row.owner_id, mime_type=row.mime_type,
        content_hash_sha256=row.content_hash_sha256, size_bytes=row.size_bytes,
        storage_reference=row.storage_reference, created_at=row.created_at)


class SqlAlchemyArtifactRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, *, owner_id: UUID, mime_type: str, content_hash_sha256: str,
                     size_bytes: int, storage_reference: str) -> InputArtifactRecord:
        row = InputArtifact(owner_id=owner_id, mime_type=mime_type,
            content_hash_sha256=content_hash_sha256, size_bytes=size_bytes,
            storage_reference=storage_reference)
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return _record(row)

    async def get(self, artifact_id: UUID) -> InputArtifactRecord | None:
        row = await self.db.get(InputArtifact, artifact_id)
        return None if row is None else _record(row)

    async def commit(self) -> None:
        await self.db.commit()
