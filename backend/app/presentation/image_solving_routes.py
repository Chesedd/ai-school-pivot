"""Thin REST adapter for the standalone image-solving bounded context."""
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.application.authoring import FailureCode, ProviderFailure
from app.application.image_solving import ImageSolvingService
from app.application.image_solving_api import ImageSolvingApplicationService
from app.config import Settings, get_settings
from app.db.session import get_session
from app.infrastructure.image_solving_repository import SqlAlchemyImageSolvingRepository
from app.infrastructure.input_artifact_repository import SqlAlchemyArtifactRepository
from app.application.input_artifacts import ArtifactOwnershipService
from app.presentation.image_solving_schemas import (
    CreateImageSolvingSessionRequest, ImageSolvingAttemptsResponse,
    ImageSolvingResultResponse, ImageSolvingSessionResponse, ImageSolvingStateResponse,
)

router = APIRouter(prefix="/api/image-solving", tags=["image-solving"])


class _UnavailablePipelinePort:
    """Fail closed until deployment injects configured storage/provider ports."""
    async def sha256(self, artifact):
        raise ProviderFailure(FailureCode.PROVIDER_UNAVAILABLE)

    async def extract(self, artifact):
        raise ProviderFailure(FailureCode.PROVIDER_UNAVAILABLE)

    async def solve(self, value):
        raise ProviderFailure(FailureCode.PROVIDER_UNAVAILABLE)


def image_solving_service(db=Depends(get_session)) -> ImageSolvingApplicationService:
    repository = SqlAlchemyImageSolvingRepository(db)
    artifacts = ArtifactOwnershipService(SqlAlchemyArtifactRepository(db))
    unavailable = _UnavailablePipelinePort()
    flow = ImageSolvingService(repository, artifacts, unavailable, unavailable, unavailable)
    return ImageSolvingApplicationService(flow, repository)


@router.post("/sessions", response_model=ImageSolvingSessionResponse, status_code=201)
async def create_session(payload: CreateImageSolvingSessionRequest, response: Response,
        service: ImageSolvingApplicationService = Depends(image_solving_service),
        settings: Settings = Depends(get_settings)):
    result = await service.create(payload, settings.content_bank_dev_actor_id)
    response.headers["Location"] = f"/api/image-solving/sessions/{result.session_id}"
    return result


@router.post("/sessions/{session_id}/run", response_model=ImageSolvingSessionResponse)
async def run_session(session_id: UUID,
        service: ImageSolvingApplicationService = Depends(image_solving_service),
        settings: Settings = Depends(get_settings)):
    return await service.run(session_id, settings.content_bank_dev_actor_id)


@router.get("/sessions/{session_id}", response_model=ImageSolvingStateResponse)
async def get_session(session_id: UUID,
        service: ImageSolvingApplicationService = Depends(image_solving_service),
        settings: Settings = Depends(get_settings)):
    return await service.state(session_id, settings.content_bank_dev_actor_id)


@router.get("/sessions/{session_id}/result", response_model=ImageSolvingResultResponse)
async def get_result(session_id: UUID,
        service: ImageSolvingApplicationService = Depends(image_solving_service),
        settings: Settings = Depends(get_settings)):
    return await service.result(session_id, settings.content_bank_dev_actor_id)


@router.get("/sessions/{session_id}/attempts", response_model=ImageSolvingAttemptsResponse)
async def get_attempts(session_id: UUID,
        service: ImageSolvingApplicationService = Depends(image_solving_service),
        settings: Settings = Depends(get_settings)):
    return await service.attempts(session_id, settings.content_bank_dev_actor_id)
