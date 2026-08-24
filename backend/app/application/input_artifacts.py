"""Authoring-owned boundary for immutable, externally stored input artifacts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
import hashlib
from uuid import UUID


SUPPORTED_ARTIFACT_MIME_TYPES = frozenset({
    "image/png", "image/jpeg", "image/webp", "application/pdf",
})
MIN_ARTIFACT_SIZE_BYTES = 1
MAX_ARTIFACT_SIZE_BYTES = 25 * 1024 * 1024
MAX_ARTIFACT_CONTEXT_LENGTH = 1000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactError(ValueError):
    """A stable, disclosure-safe application error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InputArtifactRecord:
    """Immutable metadata only; binary content belongs to external storage."""

    id: UUID
    owner_id: UUID
    mime_type: str
    content_hash_sha256: str
    size_bytes: int
    storage_reference: str
    created_at: datetime


class ArtifactRepository(Protocol):
    async def create(self, *, owner_id: UUID, mime_type: str, content_hash_sha256: str,
                     size_bytes: int, storage_reference: str) -> InputArtifactRecord: ...

    async def get(self, artifact_id: UUID) -> InputArtifactRecord | None: ...

    async def commit(self) -> None: ...


class ArtifactStoragePort(Protocol):
    """Opaque binary storage; references must never leave this boundary/API flow."""

    async def store(self, content: bytes, mime_type: str) -> str: ...
    async def read(self, storage_reference: str) -> bytes: ...
    async def delete(self, storage_reference: str) -> None: ...


def detected_mime_type(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    return None


class ArtifactUploadService:
    """Validates and durably coordinates storage followed by metadata commit."""

    def __init__(self, repository: ArtifactRepository, storage: ArtifactStoragePort):
        self.repository, self.storage = repository, storage

    async def upload(self, *, owner_id: UUID, content: bytes, claimed_mime_type: str,
                     context: str | None = None) -> InputArtifactRecord:
        if type(context) is not str and context is not None:
            raise ArtifactError("invalid_artifact_context")
        if context is not None and (
            context != context.strip() or len(context) > MAX_ARTIFACT_CONTEXT_LENGTH
        ):
            raise ArtifactError("invalid_artifact_context")
        if type(content) is not bytes or len(content) < MIN_ARTIFACT_SIZE_BYTES:
            raise ArtifactError("artifact_too_small")
        if len(content) > MAX_ARTIFACT_SIZE_BYTES:
            raise ArtifactError("artifact_too_large")
        if claimed_mime_type not in SUPPORTED_ARTIFACT_MIME_TYPES:
            raise ArtifactError("unsupported_artifact_type")
        actual = detected_mime_type(content)
        if actual is None or actual != claimed_mime_type:
            raise ArtifactError("invalid_artifact_signature")
        reference = await self.storage.store(content, actual)
        try:
            record = await self.repository.create(owner_id=owner_id, mime_type=actual,
                content_hash_sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content),
                storage_reference=reference)
            await self.repository.commit()
            return record
        except BaseException:
            await self.storage.delete(reference)
            raise


def _validate_metadata(mime_type: str, content_hash_sha256: str, size_bytes: int,
                       storage_reference: str) -> None:
    if type(mime_type) is not str or mime_type not in SUPPORTED_ARTIFACT_MIME_TYPES:
        raise ArtifactError("unsupported_artifact_type")
    if type(size_bytes) is not int or not MIN_ARTIFACT_SIZE_BYTES <= size_bytes <= MAX_ARTIFACT_SIZE_BYTES:
        raise ArtifactError("artifact_integrity_failed")
    if type(content_hash_sha256) is not str or not _SHA256.fullmatch(content_hash_sha256):
        raise ArtifactError("artifact_integrity_failed")
    if type(storage_reference) is not str or storage_reference != storage_reference.strip() or not storage_reference or len(storage_reference) > 512:
        raise ArtifactError("artifact_integrity_failed")


class ArtifactOwnershipService:
    def __init__(self, repository: ArtifactRepository):
        self.repository = repository

    async def register_artifact(self, *, owner_id: UUID, mime_type: str,
                                content_hash_sha256: str, size_bytes: int,
                                storage_reference: str) -> InputArtifactRecord:
        _validate_metadata(mime_type, content_hash_sha256, size_bytes, storage_reference)
        return await self.repository.create(owner_id=owner_id, mime_type=mime_type,
            content_hash_sha256=content_hash_sha256, size_bytes=size_bytes,
            storage_reference=storage_reference)

    async def get_owned_artifact(self, *, artifact_id: UUID, owner_id: UUID) -> InputArtifactRecord:
        artifact = await self.repository.get(artifact_id)
        if artifact is None:
            raise ArtifactError("artifact_not_found")
        if artifact.owner_id != owner_id:
            raise ArtifactError("artifact_access_denied")
        return artifact

    async def verify_integrity(self, *, artifact_id: UUID, owner_id: UUID,
                               content_hash_sha256: str, size_bytes: int | None = None) -> InputArtifactRecord:
        artifact = await self.get_owned_artifact(artifact_id=artifact_id, owner_id=owner_id)
        if (type(content_hash_sha256) is not str or not _SHA256.fullmatch(content_hash_sha256)
                or content_hash_sha256 != artifact.content_hash_sha256
                or (size_bytes is not None and size_bytes != artifact.size_bytes)):
            raise ArtifactError("artifact_integrity_failed")
        return artifact
