"""Application facade for the public image-solving API.

It is the ownership and disclosure boundary between HTTP and ImageSolvingService.
No authoring aggregate or Content Bank entity participates in this facade.
"""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.application.authoring import ProviderFailure
from app.application.image_solving import ImageSolvingError, ImageSolvingService
from app.application.input_artifacts import ArtifactError
from app.presentation.image_solving_schemas import (
    AttemptUsageResponse, CreateImageSolvingSessionRequest, ExtractionMetadataResponse, ExtractionResponse,
    ImageSolvingAttemptResponse, ImageSolvingAttemptsResponse, ImageSolvingResultResponse,
    ImageSolvingSessionResponse, ImageSolvingStateResponse, SolutionResponse,
    StageStatusResponse, TaskClassificationResponse, ValidationResponse,
)


class ImageSolvingApiError(RuntimeError):
    def __init__(self, code: str, status: int):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ImageSolvingAttempt:
    stage: str
    provider_id: str | None
    model_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_amount: object | None
    currency: str | None
    provider_request_id: str | None
    created_at: object


class AttemptReader(Protocol):
    async def attempts(self, session_id: UUID) -> tuple[ImageSolvingAttempt, ...]: ...


_ERRORS = {
    "session_not_found": ("image_solving_session_not_found", 404),
    "session_access_denied": ("image_solving_session_not_found", 404),
    "artifact_not_found": ("artifact_not_found", 404),
    "artifact_access_denied": ("artifact_access_denied", 403),
    "session_in_progress": ("image_solving_in_progress", 409),
    "invalid_checkpoint": ("invalid_artifact_or_checkpoint", 422),
    "artifact_integrity_failed": ("invalid_artifact_or_checkpoint", 422),
    "recommendation_session_incomplete": ("image_solving_not_ready", 409),
    "metadata_resolution_failed": ("image_solving_metadata_resolution_failed", 503),
}


class ImageSolvingApplicationService:
    def __init__(self, flow: ImageSolvingService, attempt_reader: AttemptReader):
        self.flow, self.attempt_reader = flow, attempt_reader

    @staticmethod
    def _raise(exc: Exception):
        if isinstance(exc, (ImageSolvingError, ArtifactError)) and exc.code in _ERRORS:
            code, status = _ERRORS[exc.code]
            raise ImageSolvingApiError(code, status) from None
        if isinstance(exc, ProviderFailure):
            raise ImageSolvingApiError("image_solving_provider_failed", 503) from None
        raise ImageSolvingApiError("image_solving_provider_failed", 503) from None

    async def create(self, request: CreateImageSolvingSessionRequest, owner_id: UUID):
        try: state = await self.flow.create_session(owner_id=owner_id, input_artifact_id=request.artifact_id)
        except Exception as exc: self._raise(exc)
        return ImageSolvingSessionResponse(session_id=state.session_id,
            artifact_id=state.input_artifact_id, status=state.lifecycle_status.value,
            failure_code=state.failure_code,
            failure_stage=self._failure_stage(state))

    async def _owned(self, session_id: UUID, owner_id: UUID):
        try: return await self.flow.get_state(session_id=session_id, owner_id=owner_id)
        except Exception as exc: self._raise(exc)

    async def run(self, session_id: UUID, owner_id: UUID):
        try: state = await self.flow.resume(session_id=session_id, owner_id=owner_id)
        except Exception as exc: self._raise(exc)
        return ImageSolvingSessionResponse(session_id=state.session_id,
            artifact_id=state.input_artifact_id, status=state.lifecycle_status.value,
            failure_code=state.failure_code,
            failure_stage=self._failure_stage(state))

    async def state(self, session_id: UUID, owner_id: UUID):
        state = await self._owned(session_id, owner_id)
        lifecycle = state.lifecycle_status.value
        stages = StageStatusResponse(
            extraction="completed" if state.extraction_checkpoint else "in_progress" if lifecycle == "extracting" else "pending",
            solver="completed" if state.solver_checkpoint else "in_progress" if lifecycle == "solving" else "pending",
            validation="completed" if state.validation_checkpoint else "pending")
        return ImageSolvingStateResponse(session_id=state.session_id,
            artifact_id=state.input_artifact_id, status=lifecycle, stages=stages,
            failure_code=state.failure_code, failure_stage=self._failure_stage(state),
            created_at=state.created_at, updated_at=state.updated_at)

    @staticmethod
    def _failure_stage(state):
        if state.lifecycle_status.value != "failed": return None
        if state.extraction_checkpoint is None: return "extraction"
        if state.solver_checkpoint is None: return "solver"
        return "validation"

    async def result(self, session_id: UUID, owner_id: UUID):
        state = await self._owned(session_id, owner_id)
        e, s, v = state.extraction_checkpoint, state.solver_checkpoint, state.validation_checkpoint
        if e is None or s is None or v is None:
            raise ImageSolvingApiError("image_solving_not_ready", 409)
        return ImageSolvingResultResponse(session_id=state.session_id, artifact_id=state.input_artifact_id,
            extraction=ExtractionResponse(extracted_text=e.extracted_text,
                structured_statement=e.structured_statement,
                task_classification=TaskClassificationResponse(task_type=e.detected_task_type,
                    answer_format=e.detected_answer_format), confidence=e.extraction_confidence,
                choices=e.choices, metadata=ExtractionMetadataResponse(**e.metadata.model_dump())),
            solution=SolutionResponse(answer=s.final_answer, reasoning_summary=s.reasoning_summary,
                confidence=s.confidence),
            validation=ValidationResponse(status=v.validation_status, findings=v.findings,
                manual_review=v.requires_human_review))

    async def attempts(self, session_id: UUID, owner_id: UUID):
        await self._owned(session_id, owner_id)
        rows = await self.attempt_reader.attempts(session_id)
        return ImageSolvingAttemptsResponse(items=tuple(ImageSolvingAttemptResponse(
            stage=row.stage, provider=row.provider_id, model=row.model_id,
            usage=AttemptUsageResponse(input_tokens=row.input_tokens, output_tokens=row.output_tokens),
            cost=row.cost_amount, currency=row.currency, latency_ms=None,
            request_id=row.provider_request_id, created_at=row.created_at) for row in rows))
