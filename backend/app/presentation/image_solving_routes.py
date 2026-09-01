"""Thin REST adapter for the standalone image-solving bounded context."""
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, Response

from app.application.authoring import FailureCode, ModelRoute, ProviderFailure
from app.application.image_solving import ImageSolvingService
from app.application.principal import Principal
from app.application.capabilities import IMAGE_SOLVING_USE
from app.application.image_solving_api import ImageSolvingApiError, ImageSolvingApplicationService
from app.application.image_solving_promotion import PromoteImageSolvingService
from app.application.image_solving_metadata import MetadataRecommendationService, ImageTaskMetadataRecommendationV1
from app.infrastructure.image_solving_metadata import SqlAlchemyMetadataCatalogLoader
from app.config import Settings, get_settings
from app.db.session import get_session as get_db_session
from app.infrastructure.image_solving_repository import SqlAlchemyImageSolvingRepository
from app.infrastructure.input_artifact_repository import SqlAlchemyArtifactRepository
from app.infrastructure.artifact_storage import (DatabaseArtifactStorageReader,
    FilesystemArtifactStorage)
from app.infrastructure.extraction_providers import (AnthropicExtractionAdapter,
    AnthropicSolverAdapter, RoutedAnthropicExtractor)
from app.application.input_artifacts import ArtifactOwnershipService
from app.presentation.auth_dependencies import require_capability, require_trusted_origin
from app.presentation.image_solving_schemas import (
    CreateImageSolvingSessionRequest, ImageSolvingAttemptsResponse,
    ImageSolvingResultResponse, ImageSolvingSessionResponse, ImageSolvingStateResponse,
    PromoteImageSolvingRequest, PromoteImageSolvingResponse,
)

router = APIRouter(prefix="/api/image-solving", tags=["image-solving"], dependencies=[Depends(require_trusted_origin)])


class _UnavailablePipelinePort:
    """Fail closed until deployment injects configured storage/provider ports."""
    async def sha256(self, artifact):
        raise ProviderFailure(FailureCode.PROVIDER_UNAVAILABLE)

    async def extract(self, artifact):
        raise ProviderFailure(FailureCode.PROVIDER_UNAVAILABLE)

    async def solve(self, value):
        raise ProviderFailure(FailureCode.PROVIDER_UNAVAILABLE)


def _build_image_solving_flow(db: AsyncSession, settings: Settings) -> ImageSolvingService:
    """Pure composition root: unlike FastAPI dependencies, arguments are concrete."""
    repository = SqlAlchemyImageSolvingRepository(db)
    artifact_repository = SqlAlchemyArtifactRepository(db)
    artifacts = ArtifactOwnershipService(artifact_repository)
    if settings.anthropic_credential:
        from anthropic import AsyncAnthropic
        client_options = {"base_url": settings.anthropic_base_url}
        if settings.anthropic_auth_token:
            client_options["auth_token"] = settings.anthropic_credential
        else:
            client_options["api_key"] = settings.anthropic_credential
        client = AsyncAnthropic(**client_options)
        storage = DatabaseArtifactStorageReader(artifact_repository,
            FilesystemArtifactStorage(settings.artifact_storage_path))
        route = ModelRoute("anthropic", settings.image_solving_anthropic_model)
        extractor = RoutedAnthropicExtractor(AnthropicExtractionAdapter(client, storage), route)
        flow = ImageSolvingService(repository, artifacts, storage, extractor,
            AnthropicSolverAdapter(client, route))
    else:
        unavailable = _UnavailablePipelinePort()
        flow = ImageSolvingService(repository, artifacts, unavailable, unavailable, unavailable)
    return flow


def image_solving_service(db=Depends(get_db_session),
        settings: Settings = Depends(get_settings)) -> ImageSolvingApplicationService:
    flow = _build_image_solving_flow(db, settings)
    return ImageSolvingApplicationService(flow, flow.repository)


@router.post("/sessions", response_model=ImageSolvingSessionResponse, status_code=201)
async def create_session(payload: CreateImageSolvingSessionRequest, response: Response,
        service: ImageSolvingApplicationService = Depends(image_solving_service),
        principal: Principal = Depends(require_capability(IMAGE_SOLVING_USE))):
    result = await service.create(payload, principal.user_id)
    response.headers["Location"] = f"/api/image-solving/sessions/{result.session_id}"
    return result


@router.post("/sessions/{session_id}/run", response_model=ImageSolvingSessionResponse)
async def run_session(session_id: UUID,
        service: ImageSolvingApplicationService = Depends(image_solving_service),
        principal: Principal = Depends(require_capability(IMAGE_SOLVING_USE))):
    return await service.run(session_id, principal.user_id)


@router.get("/sessions/{session_id}", response_model=ImageSolvingStateResponse)
async def get_session(session_id: UUID,
        service: ImageSolvingApplicationService = Depends(image_solving_service),
        principal: Principal = Depends(require_capability(IMAGE_SOLVING_USE))):
    return await service.state(session_id, principal.user_id)


@router.get("/sessions/{session_id}/result", response_model=ImageSolvingResultResponse)
async def get_result(session_id: UUID,
        service: ImageSolvingApplicationService = Depends(image_solving_service),
        principal: Principal = Depends(require_capability(IMAGE_SOLVING_USE))):
    return await service.result(session_id, principal.user_id)


@router.get("/sessions/{session_id}/attempts", response_model=ImageSolvingAttemptsResponse)
async def get_attempts(session_id: UUID,
        service: ImageSolvingApplicationService = Depends(image_solving_service),
        principal: Principal = Depends(require_capability(IMAGE_SOLVING_USE))):
    return await service.attempts(session_id, principal.user_id)

def metadata_service(db: AsyncSession, settings: Settings):
    repository=SqlAlchemyImageSolvingRepository(db)
    flow = _build_image_solving_flow(db, settings)
    return MetadataRecommendationService(flow,repository,
        SqlAlchemyMetadataCatalogLoader(db))

@router.get("/sessions/{session_id}/recommendations",response_model=ImageTaskMetadataRecommendationV1)
async def get_recommendations(session_id:UUID,db=Depends(get_db_session),settings:Settings=Depends(get_settings),principal:Principal=Depends(require_capability(IMAGE_SOLVING_USE))):
    try:
        result=await metadata_service(db,settings).get(session_id,principal.user_id)
        if result is None: raise ImageSolvingApiError("image_solving_recommendations_not_found",404)
        return result
    except ImageSolvingApiError: raise
    except Exception as exc: ImageSolvingApplicationService._raise(exc)

@router.post("/sessions/{session_id}/recommendations",response_model=ImageTaskMetadataRecommendationV1)
async def create_recommendations(session_id:UUID,db=Depends(get_db_session),settings:Settings=Depends(get_settings),principal:Principal=Depends(require_capability(IMAGE_SOLVING_USE))):
    try:return await metadata_service(db,settings).generate(session_id,principal.user_id)
    except Exception as exc: ImageSolvingApplicationService._raise(exc)


@router.post("/sessions/{session_id}/promote", response_model=PromoteImageSolvingResponse)
async def promote_session(session_id: UUID, payload: PromoteImageSolvingRequest,
        db=Depends(get_db_session), principal: Principal = Depends(require_capability(IMAGE_SOLVING_USE))):
    return await PromoteImageSolvingService(db).promote(
        session_id, principal.user_id, payload)
