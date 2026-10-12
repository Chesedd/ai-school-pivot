"""Filesystem implementation of the private artifact binary boundary."""
import hashlib
import re
from pathlib import Path
from uuid import UUID, uuid4


class FilesystemArtifactStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, reference: str) -> Path:
        if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reference):
            raise ValueError("invalid storage reference")
        path = self.root / reference
        if path.is_symlink():
            raise ValueError("invalid storage reference")
        return path

    async def store(self, content: bytes, mime_type: str) -> str:
        del mime_type
        self.root.mkdir(parents=True, exist_ok=True)
        reference = str(uuid4())
        self._path(reference).write_bytes(content)
        return reference

    async def read(self, storage_reference: str) -> bytes:
        return self._path(storage_reference).read_bytes()

    async def read_bounded(self, storage_reference: str, max_bytes: int) -> bytes:
        """Read at most the remaining budget plus one rejection sentinel byte."""
        with self._path(storage_reference).open("rb") as source:
            return source.read(max_bytes + 1)

    async def delete(self, storage_reference: str) -> bool:
        """Delete exactly one opaque object; False distinguishes confirmed absence."""
        try:
            self._path(storage_reference).unlink()
        except FileNotFoundError:
            return False
        return True


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
