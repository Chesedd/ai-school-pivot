"""Image-solving application flow with durable, resumable checkpoints."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import logging
from time import monotonic
from typing import Protocol
from uuid import UUID

from app.application.extraction_pipeline import SolverInputV1
from app.application.image_solving_contracts import (
    ExtractionResultV1, ImageSolvingSession, ImageSolvingStatus, InputArtifactV1,
    SolutionResultV1, ValidationResultV1, ValidationStatus,
)
from app.application.input_artifacts import ArtifactOwnershipService, InputArtifactRecord
from app.application.authoring import FailureCode, ProviderFailure

logger = logging.getLogger(__name__)


class ImageSolvingError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ImageSolvingRepository(Protocol):
    async def create(self, owner_id: UUID, artifact_id: UUID) -> ImageSolvingSession: ...
    async def get(self, session_id: UUID) -> ImageSolvingSession | None: ...
    async def claim(self, session_id: UUID, expected: ImageSolvingStatus,
                    running: ImageSolvingStatus) -> bool: ...
    async def save_checkpoint(self, session_id: UUID, stage: str, payload: object,
                              status: ImageSolvingStatus, **kwargs) -> ImageSolvingSession: ...
    async def fail(self, session_id: UUID, code: str) -> None: ...
    async def retry_solver(self, session_id: UUID) -> bool: ...


class ArtifactIntegrityVerifier(Protocol):
    """Storage boundary computes SHA-256; bytes never cross into solver input."""
    async def sha256(self, artifact: InputArtifactRecord) -> str: ...


class Extractor(Protocol):
    async def extract(self, artifact: InputArtifactV1) -> ExtractionResultV1: ...


class Solver(Protocol):
    async def solve(self, value: SolverInputV1) -> SolutionResultV1: ...


class DeterministicImageValidator:
    """Conservative validation with no provider or LLM dependency."""
    def validate(self, extraction: ExtractionResultV1, solution: SolutionResultV1) -> ValidationResultV1:
        extraction_ok = extraction.extraction_confidence >= Decimal("0.80")
        ocr_ok = not extraction.ocr_issues
        solver_ok = solution.status == "solved"
        answer_ok = bool(solution.final_answer.strip()) and solution.confidence >= Decimal("0.50")
        checks = (extraction_ok, ocr_ok, solver_ok, answer_ok)
        findings = tuple(name for name, ok in zip(
            ("low_extraction_confidence", "ocr_quality_issue", "solver_status_invalid", "answer_inconsistent"),
            checks, strict=True) if not ok)
        status = (ValidationStatus.VALIDATED if all(checks) else
                  ValidationStatus.FAILED if not solver_ok or not answer_ok else ValidationStatus.NEEDS_REVIEW)
        return ValidationResultV1(validation_status=status,
            confidence=min(extraction.extraction_confidence, solution.confidence), findings=findings,
            requires_human_review=status is not ValidationStatus.VALIDATED,
            extraction_confidence_check=extraction_ok, OCR_quality_check=ocr_ok,
            solver_status_check=solver_ok, answer_consistency_check=answer_ok)


class ImageSolvingService:
    def __init__(self, repository: ImageSolvingRepository, artifacts: ArtifactOwnershipService,
                 integrity: ArtifactIntegrityVerifier, extractor: Extractor, solver: Solver,
                 validator: DeterministicImageValidator | None = None):
        self.repository, self.artifacts, self.integrity = repository, artifacts, integrity
        self.extractor, self.solver = extractor, solver
        self.validator = validator or DeterministicImageValidator()

    async def create_session(self, *, owner_id: UUID, input_artifact_id: UUID) -> ImageSolvingSession:
        await self.artifacts.get_owned_artifact(artifact_id=input_artifact_id, owner_id=owner_id)
        return await self.repository.create(owner_id, input_artifact_id)

    async def get_state(self, *, session_id: UUID, owner_id: UUID) -> ImageSolvingSession:
        try:
            state = await self.repository.get(session_id)
        except (ValueError, TypeError):
            raise ImageSolvingError("invalid_checkpoint") from None
        if state is None: raise ImageSolvingError("session_not_found")
        if state.owner_id != owner_id: raise ImageSolvingError("session_access_denied")
        self._validate_checkpoint(state)
        return state

    async def run(self, *, session_id: UUID, owner_id: UUID,
                  user_context: str = "Solve the task shown in this artifact") -> ImageSolvingSession:
        return await self.resume(session_id=session_id, owner_id=owner_id, user_context=user_context)

    async def resume(self, *, session_id: UUID, owner_id: UUID,
                     user_context: str = "Solve the task shown in this artifact") -> ImageSolvingSession:
        state = await self.get_state(session_id=session_id, owner_id=owner_id)
        retried_failure_code = None
        if state.validation_checkpoint is not None:
            return state
        if state.lifecycle_status is ImageSolvingStatus.FAILED:
            if state.extraction_checkpoint is None or state.solver_checkpoint is not None:
                return state
            retried_failure_code = state.failure_code
            if not await self.repository.retry_solver(session_id):
                return state
            state = await self.get_state(session_id=session_id, owner_id=owner_id)
        artifact = await self.artifacts.get_owned_artifact(artifact_id=state.input_artifact_id, owner_id=owner_id)
        if await self.integrity.sha256(artifact) != artifact.content_hash_sha256:
            await self.repository.fail(session_id, "artifact_integrity_failed")
            raise ImageSolvingError("artifact_integrity_failed")
        try:
            stage = "extraction"
            extraction = state.extraction_checkpoint
            if extraction is None:
                if not await self.repository.claim(session_id, ImageSolvingStatus.CREATED, ImageSolvingStatus.EXTRACTING):
                    raise ImageSolvingError("session_in_progress")
                payload = InputArtifactV1(artifact_id=str(artifact.id), mime_type=artifact.mime_type,
                    content_hash=artifact.content_hash_sha256, user_context=user_context)
                extraction = await self.extractor.extract(payload)
                state = await self.repository.save_checkpoint(session_id, "extraction", extraction,
                    ImageSolvingStatus.EXTRACTED, **self._checkpoint_telemetry(self.extractor))
            stage = "solver"
            solution = state.solver_checkpoint
            if solution is None:
                if not await self.repository.claim(session_id, ImageSolvingStatus.EXTRACTED, ImageSolvingStatus.SOLVING):
                    raise ImageSolvingError("session_in_progress")
                solver_input = SolverInputV1.from_extraction(extraction)
                attempt_started = monotonic()
                retry_count = 0
                try:
                    solution = await self.solver.solve(solver_input)
                except ProviderFailure as exc:
                    if exc.code not in {FailureCode.TIMEOUT, FailureCode.CONNECTION_ERROR,
                                        FailureCode.PROVIDER_UNAVAILABLE}:
                        raise
                    retry_count = 1
                    await asyncio.sleep(0.1)
                    solution = await self.solver.solve(solver_input)
                state = await self.repository.save_checkpoint(session_id, "solver", solution,
                    ImageSolvingStatus.SOLVED, **self._checkpoint_telemetry(self.solver))
            stage = "validation"
            validation = self.validator.validate(extraction, solution)
            return await self.repository.save_checkpoint(session_id, "validation", validation,
                ImageSolvingStatus.VALIDATED)
        except ImageSolvingError:
            raise
        except Exception as exc:
            port = self.extractor if stage == "extraction" else self.solver if stage == "solver" else None
            failure_code = exc.code.value if isinstance(exc, ProviderFailure) else None
            failure_code = failure_code or "unexpected_internal_error"
            telemetry = getattr(port, "last_telemetry", None)
            latency_ms = (getattr(telemetry, "latency_ms", None) if telemetry else
                max(0, int((monotonic() - attempt_started) * 1000)) if stage == "solver" else None)
            detail = getattr(exc, "adapter_detail", "") or "none"
            # Put diagnostics in the message because the production formatter does
            # not render LogRecord ``extra`` fields.
            logger.error(
                "image solving stage failed session_id=%s stage=%s provider=%s "
                "model=%s failure_code=%s exception=%s detail=%r request_id=%s "
                "latency_ms=%s retry_count=%s solver_provider_calls=%s "
                "repair_used=%s first_stop_reason=%s",
                session_id, stage, getattr(port, "provider_id", "unknown"),
                getattr(port, "model_id", "unknown"), failure_code,
                type(exc).__name__, detail,
                getattr(telemetry, "provider_request_id", None), latency_ms,
                retry_count if stage == "solver" else 0,
                getattr(telemetry, "provider_calls", 0),
                getattr(telemetry, "repair_used", False),
                getattr(telemetry, "first_stop_reason", None), extra={
                "session_id": str(session_id), "stage": stage,
                "provider": getattr(port, "provider_id", "unknown"),
                "model": getattr(port, "model_id", "unknown"),
                "failure_code": failure_code,
                "exception_category": type(exc).__name__,
                "validation_reason": getattr(exc, "adapter_detail", "") or None,
            })
            persisted_code = ("malformed_response_retried"
                if retried_failure_code == "malformed_response" and
                failure_code == "malformed_response" else failure_code)
            await self.repository.fail(session_id, persisted_code)
            raise

    @staticmethod
    def _checkpoint_telemetry(port: object) -> dict[str, object]:
        telemetry = getattr(port, "last_telemetry", None)
        route = getattr(port, "route", None)
        return {"route": route, "telemetry": telemetry} if route is not None and telemetry is not None else {}

    @staticmethod
    def _validate_checkpoint(state: ImageSolvingSession) -> None:
        e, s, v = state.extraction_checkpoint, state.solver_checkpoint, state.validation_checkpoint
        broken = (s is not None and e is None) or (v is not None and (e is None or s is None))
        expected = ({ImageSolvingStatus.CREATED, ImageSolvingStatus.EXTRACTING} if e is None else
                    {ImageSolvingStatus.EXTRACTED, ImageSolvingStatus.SOLVING} if s is None else
                    {ImageSolvingStatus.SOLVED} if v is None else {ImageSolvingStatus.VALIDATED})
        if broken or state.lifecycle_status not in expected | {ImageSolvingStatus.FAILED}:
            raise ImageSolvingError("invalid_checkpoint")
