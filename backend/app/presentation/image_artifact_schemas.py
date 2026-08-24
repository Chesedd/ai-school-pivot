from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class ImageArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: UUID
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
