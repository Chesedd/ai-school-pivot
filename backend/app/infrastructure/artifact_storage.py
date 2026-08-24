"""Filesystem implementation of the private artifact binary boundary."""
from pathlib import Path
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
