"""Upload-only public boundary for image-solving input artifacts."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from app.application.input_artifacts import (ArtifactError, ArtifactOwnershipService,
    ArtifactUploadService, MAX_ARTIFACT_SIZE_BYTES)
from app.config import Settings, get_settings
from app.db.session import get_session
from app.infrastructure.artifact_storage import FilesystemArtifactStorage
from app.infrastructure.input_artifact_repository import SqlAlchemyArtifactRepository
from app.presentation.image_artifact_schemas import ImageArtifactResponse

router = APIRouter(prefix="/api/image-solving/artifacts", tags=["image-solving"])
_UPLOAD_FIELDS = frozenset({"file", "context"})

def response(record) -> ImageArtifactResponse:
    return ImageArtifactResponse(artifact_id=record.id, mime_type=record.mime_type,
        size_bytes=record.size_bytes, sha256=record.content_hash_sha256,
        created_at=record.created_at)

def service(db=Depends(get_session), settings: Settings = Depends(get_settings)):
    repository = SqlAlchemyArtifactRepository(db)
    return ArtifactUploadService(repository, FilesystemArtifactStorage(settings.artifact_storage_path))

def translate(exc: ArtifactError) -> HTTPException:
    status = 413 if exc.code in {"artifact_too_small", "artifact_too_large"} else 415 if exc.code in {
        "unsupported_artifact_type", "invalid_artifact_signature"} else 422
    return HTTPException(status, exc.code)

@router.post("/", response_model=ImageArtifactResponse, status_code=201)
async def upload(request: Request,
        upload_service: ArtifactUploadService = Depends(service),
        settings: Settings = Depends(get_settings)):
    form = await request.form()
    # Keep server-owned metadata out of the public contract. Rejecting unknown
    # fields also prevents clients from assuming that a supplied value was used.
    if any(key not in _UPLOAD_FIELDS for key in form):
        raise HTTPException(422, "unexpected upload field")
    if len(form.getlist("file")) != 1 or len(form.getlist("context")) > 1:
        raise HTTPException(422, "duplicate upload field")
    file, context = form.get("file"), form.get("context")
    if not isinstance(file, UploadFile):
        raise HTTPException(422, "file is required")
    if context is not None and not isinstance(context, str):
        raise HTTPException(422, "invalid_artifact_context")
    content = await file.read(MAX_ARTIFACT_SIZE_BYTES + 1)
    try:
        return response(await upload_service.upload(owner_id=settings.content_bank_dev_actor_id,
            content=content, claimed_mime_type=file.content_type or "", context=context))
    except ArtifactError as exc:
        raise translate(exc) from None
    finally:
        await file.close()

@router.get("/{artifact_id}", response_model=ImageArtifactResponse)
async def metadata(artifact_id: UUID, db=Depends(get_session),
        settings: Settings = Depends(get_settings)):
    try:
        record = await ArtifactOwnershipService(SqlAlchemyArtifactRepository(db)).get_owned_artifact(
            artifact_id=artifact_id, owner_id=settings.content_bank_dev_actor_id)
    except ArtifactError:
        raise HTTPException(404, "artifact_not_found") from None
    return response(record)
