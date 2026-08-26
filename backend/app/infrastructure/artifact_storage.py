"""Filesystem implementation of the private artifact binary boundary."""
import hashlib
from pathlib import Path
from uuid import UUID
from uuid import uuid4


class FilesystemArtifactStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, reference: str) -> Path:
        if not reference or Path(reference).name != reference:
            raise ValueError("invalid storage reference")
        return self.root / reference

    async def store(self, content: bytes, mime_type: str) -> str:
        del mime_type
        self.root.mkdir(parents=True, exist_ok=True)
        reference = str(uuid4())
        self._path(reference).write_bytes(content)
        return reference

    async def read(self, storage_reference: str) -> bytes:
        return self._path(storage_reference).read_bytes()

    async def delete(self, storage_reference: str) -> None:
        self._path(storage_reference).unlink(missing_ok=True)


class DatabaseArtifactStorageReader:
    """Resolve private storage references without exposing them to provider DTOs."""
    def __init__(self, repository, storage: FilesystemArtifactStorage):
        self.repository, self.storage = repository, storage

    async def read_artifact_bytes(self, artifact_id: str) -> bytes:
        try:
            artifact = await self.repository.get(UUID(artifact_id))
        except (TypeError, ValueError):
            artifact = None
        if artifact is None:
            raise ValueError("artifact_not_found")
        return await self.storage.read(artifact.storage_reference)

    async def sha256(self, artifact) -> str:
        return hashlib.sha256(await self.storage.read(artifact.storage_reference)).hexdigest()
