from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.input_artifacts import (
    MAX_ARTIFACT_SIZE_BYTES, ArtifactError, ArtifactOwnershipService,
    InputArtifactRecord,
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
        content_hash_sha256="a" * 64, size_bytes=100,
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
        content_hash_sha256="a" * 64, size_bytes=100) == artifact
    with pytest.raises(ArtifactError, match="artifact_access_denied"):
        await service.get_owned_artifact(artifact_id=artifact.id, owner_id=uuid4())
    with pytest.raises(ArtifactError, match="artifact_integrity_failed"):
        await service.verify_integrity(artifact_id=artifact.id, owner_id=owner,
            content_hash_sha256="b" * 64)
    with pytest.raises(ArtifactError, match="artifact_not_found"):
        await service.get_owned_artifact(artifact_id=uuid4(), owner_id=owner)
