"""Ports and routing contracts for multimodal extraction.

The artifact DTO contains metadata only.  Resolving its bytes is deliberately an
infrastructure responsibility so application services never handle image data.
"""
from __future__ import annotations

from typing import Protocol

from app.application.authoring import ModelRoute
from app.application.image_solving_contracts import ExtractionResultV1, InputArtifactV1


# A distinct name makes configuration intent explicit while retaining the one
# provider-neutral route contract used by ProviderRegistry.
ExtractionRoute = ModelRoute


class ExtractorPort(Protocol):
    async def extract(self, artifact: InputArtifactV1, route: ModelRoute) -> ExtractionResultV1: ...


class StorageReadPort(Protocol):
    async def read_artifact_bytes(self, artifact_id: str) -> bytes: ...
