"""Filesystem implementation of the private artifact binary boundary."""
import hashlib
import logging
import os
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
        if reference.startswith("scan-"):
            path = path / "content"
        if path.is_symlink():
            raise ValueError("invalid storage reference")
        return path

    async def store(self, content: bytes, mime_type: str) -> str:
        del mime_type
        self.root.mkdir(parents=True, exist_ok=True)
        reference = str(uuid4())
        path = self._path(reference)
        try:
            path.write_bytes(content)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return reference

    async def _write_new(self, path, content):
        # Exclusive allocation is the ownership proof; never replace shared bytes.
        with path.open("xb") as target:
            try:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            except BaseException:
                target.close()
                path.unlink(missing_ok=True)
                raise

    def scan_marker(self, batch_id, reference):
        if not reference.startswith("scan-"):
            raise ValueError("not a scan-owned object")
        self._path(reference)
        return self.root / ".scan-pending" / str(UUID(str(batch_id))) / reference

    async def store_scan(self, content, mime_type, batch_id):
        """Persist exact ownership BEFORE bytes; caller holds the batch DB lock.

        The scan journal lives with the objects and survives database rollback,
        cancellation and process death. Other artifact writes never enter it.
        """
        del mime_type
        self.root.mkdir(parents=True, exist_ok=True)
        while True:
            reference = "scan-" + str(uuid4())
            try:
                # Reserve an exclusive namespace before recording ownership.
                # A collision can never journal/delete a pre-existing object.
                (self.root / reference).mkdir()
                break
            except FileExistsError:
                continue
        marker = self.scan_marker(batch_id, reference)
        marker.parent.mkdir(parents=True, exist_ok=True)
        with marker.open("xb") as journal:
            journal.flush()
            os.fsync(journal.fileno())
        # Even an interrupted partial write or failed compensation remains known.
        try:
            await self._write_new(self._path(reference), content)
        except BaseException:
            try:
                await self.delete(reference)
                self.acknowledge_scan(batch_id, reference)
            except Exception:  # noqa: BLE001 - failed compensation retains its marker.
                logging.getLogger(__name__).warning("scan object cleanup deferred")
            raise
        return reference

    def acknowledge_scan(self, batch_id, reference):
        self.scan_marker(batch_id, reference).unlink(missing_ok=True)

    def pending_scan_objects(self):
        directory = self.root / ".scan-pending"
        return [(UUID(folder.name), marker.name) for folder in directory.iterdir()
                if folder.is_dir() and not folder.is_symlink()
                for marker in folder.iterdir() if marker.is_file() and not marker.is_symlink()] if directory.exists() else []

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
            deleted = False
        else:
            deleted = True
        if storage_reference.startswith("scan-"):
            try:
                (self.root / storage_reference).rmdir()
            except OSError:
                # Directory housekeeping is not artifact-byte deletion. Once
                # content is absent, a retry must still report successful absence.
                logging.getLogger(__name__).warning("empty scan namespace cleanup deferred")
        return deleted


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
