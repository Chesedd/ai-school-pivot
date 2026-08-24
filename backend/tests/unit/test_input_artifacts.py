from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.input_artifacts import (
    MAX_ARTIFACT_SIZE_BYTES, MIN_ARTIFACT_SIZE_BYTES, ArtifactError,
    ArtifactOwnershipService, ArtifactUploadService, InputArtifactRecord,
)


class MemoryRepository:
    def __init__(self): self.rows = {}

    async def create(self, **values):
        row = InputArtifactRecord(id=uuid4(), created_at=datetime.now(UTC), **values)
        self.rows[row.id] = row
        return row

    async def get(self, artifact_id): return self.rows.get(artifact_id)


async def register(service, **changes):
    values = dict(owner_id=uuid4(), mime_type="image/png",
        content_hash_sha256="a" * 64, size_bytes=MIN_ARTIFACT_SIZE_BYTES,
        storage_reference="objects/input/one")
    values.update(changes)
    return await service.register_artifact(**values)


@pytest.mark.parametrize("mime", ["image/png; charset=utf-8", "IMAGE/PNG", "image/gif", " image/png"])
async def test_mime_validation_is_an_exact_allowlist(mime):
    with pytest.raises(ArtifactError, match="unsupported_artifact_type"):
        await register(ArtifactOwnershipService(MemoryRepository()), mime_type=mime)


@pytest.mark.parametrize("size", [0, -1, MAX_ARTIFACT_SIZE_BYTES + 1, True, 1.5])
async def test_size_is_bounded_and_strict(size):
    with pytest.raises(ArtifactError, match="artifact_integrity_failed"):
        await register(ArtifactOwnershipService(MemoryRepository()), size_bytes=size)


async def test_record_metadata_is_immutable():
    artifact = await register(ArtifactOwnershipService(MemoryRepository()))
    with pytest.raises(FrozenInstanceError): artifact.content_hash_sha256 = "b" * 64
    with pytest.raises(FrozenInstanceError): artifact.owner_id = uuid4()


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "g" * 64, "sha256:" + "a" * 64])
async def test_hash_validation_rejects_noncanonical_sha256(digest):
    with pytest.raises(ArtifactError, match="artifact_integrity_failed"):
        await register(ArtifactOwnershipService(MemoryRepository()), content_hash_sha256=digest)


async def test_integrity_and_ownership_are_enforced_without_leaking_metadata():
    service = ArtifactOwnershipService(MemoryRepository())
    owner = uuid4()
    artifact = await register(service, owner_id=owner)
    assert await service.get_owned_artifact(artifact_id=artifact.id, owner_id=owner) == artifact
    assert await service.verify_integrity(artifact_id=artifact.id, owner_id=owner,
        content_hash_sha256="a" * 64, size_bytes=MIN_ARTIFACT_SIZE_BYTES) == artifact
    with pytest.raises(ArtifactError, match="artifact_access_denied"):
        await service.get_owned_artifact(artifact_id=artifact.id, owner_id=uuid4())
    with pytest.raises(ArtifactError, match="artifact_integrity_failed"):
        await service.verify_integrity(artifact_id=artifact.id, owner_id=owner,
            content_hash_sha256="b" * 64)
    with pytest.raises(ArtifactError, match="artifact_not_found"):
        await service.get_owned_artifact(artifact_id=uuid4(), owner_id=owner)


class MemoryStorage:
    def __init__(self): self.values = {}; self.deleted = []
    async def store(self, content, mime_type): self.values["private-ref"] = content; return "private-ref"
    async def read(self, reference): return self.values[reference]
    async def delete(self, reference): self.deleted.append(reference); self.values.pop(reference, None)


def payload(prefix: bytes) -> bytes:
    return prefix + b"x" * (MIN_ARTIFACT_SIZE_BYTES - len(prefix))


@pytest.mark.parametrize(("mime", "prefix"), [
    ("image/png", b"\x89PNG\r\n\x1a\n"), ("image/jpeg", b"\xff\xd8\xff"),
    ("image/webp", b"RIFF\x00\x00\x00\x00WEBP"),
    ("application/pdf", b"%PDF-1.7\n"),
])
async def test_upload_validates_signature_and_calculates_sha256(mime, prefix):
    import hashlib
    repository, storage = MemoryRepository(), MemoryStorage()
    repository.commit = lambda: _done()
    content = payload(prefix)
    artifact = await ArtifactUploadService(repository, storage).upload(owner_id=uuid4(),
        content=content, claimed_mime_type=mime)
    assert artifact.content_hash_sha256 == hashlib.sha256(content).hexdigest()
    assert artifact.storage_reference == "private-ref"


async def _done(): pass


@pytest.mark.parametrize(("mime", "content", "code"), [
    ("image/gif", payload(b"GIF89a"), "unsupported_artifact_type"),
    ("image/png", payload(b"not-png"), "invalid_artifact_signature"),
    ("image/png", b"\x89PNG\r\n\x1a\n", "artifact_too_small"),
    ("image/png", b"x" * (MAX_ARTIFACT_SIZE_BYTES + 1), "artifact_too_large"),
])
async def test_upload_rejects_unsafe_input_before_storage(mime, content, code):
    repository, storage = MemoryRepository(), MemoryStorage()
    with pytest.raises(ArtifactError, match=code):
        await ArtifactUploadService(repository, storage).upload(owner_id=uuid4(), content=content,
            claimed_mime_type=mime)
    assert storage.values == {}


async def test_failed_commit_deletes_stored_binary():
    repository, storage = MemoryRepository(), MemoryStorage()
    async def fail(): raise RuntimeError("db failed")
    repository.commit = fail
    with pytest.raises(RuntimeError, match="db failed"):
        await ArtifactUploadService(repository, storage).upload(owner_id=uuid4(),
            content=payload(b"%PDF-1.7\n"), claimed_mime_type="application/pdf")
    assert storage.deleted == ["private-ref"]


async def test_public_response_does_not_leak_storage_reference():
    from app.presentation.image_artifact_routes import response
    repository, storage = MemoryRepository(), MemoryStorage()
    repository.commit = lambda: _done()
    artifact = await ArtifactUploadService(repository, storage).upload(owner_id=uuid4(),
        content=payload(b"\xff\xd8\xff"), claimed_mime_type="image/jpeg")
    public = response(artifact).model_dump(mode="json")
    assert set(public) == {"artifact_id", "mime_type", "size_bytes", "sha256", "created_at"}
    assert "private-ref" not in str(public)
