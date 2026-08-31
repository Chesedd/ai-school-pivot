from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application.image_solving import ImageSolvingError
from app.application.image_solving_api import ImageSolvingApiError, ImageSolvingApplicationService
from app.application.image_solving_contracts import (ExtractionResultV1, ImageSolvingSession,
    ImageSolvingStatus, SolutionResultV1, ValidationResultV1)
from app.presentation.image_solving_schemas import CreateImageSolvingSessionRequest


def state(owner=None, status=ImageSolvingStatus.CREATED, complete=False):
    now = datetime.now(UTC)
    extraction = ExtractionResultV1(extracted_text="2 + 2", structured_statement="2 + 2",
        detected_task_type="calculation", detected_answer_format="number", choices=None,
        extraction_confidence=Decimal(".98"), ocr_issues=(), metadata={"title":"Сложение чисел","subject":"Математика","grade":1,"topic":"Сложение","subtopic":"Натуральные числа","skills":("Складывать числа",),"task_type":"calculation","answer_format":"number","difficulty":1,"tags":()}) if complete else None
    solution = SolutionResultV1(status="solved", reasoning_summary="Add both values.",
        final_answer="4", confidence=Decimal(".99")) if complete else None
    validation = ValidationResultV1(validation_status="validated", confidence=Decimal(".98"),
        findings=(), requires_human_review=False, extraction_confidence_check=True,
        OCR_quality_check=True, solver_status_check=True, answer_consistency_check=True) if complete else None
    return ImageSolvingSession(session_id=uuid4(), owner_id=owner or uuid4(),
        input_artifact_id=uuid4(), extraction_checkpoint=extraction,
        solver_checkpoint=solution, validation_checkpoint=validation,
        lifecycle_status=ImageSolvingStatus.VALIDATED if complete else status,
        created_at=now, updated_at=now)


class Flow:
    def __init__(self, value): self.value = value
    async def create_session(self, **kwargs): return self.value
    async def get_state(self, *, session_id, owner_id):
        if owner_id != self.value.owner_id: raise ImageSolvingError("session_access_denied")
        return self.value
    async def resume(self, **kwargs): return await self.get_state(**kwargs)


class Attempts:
    async def attempts(self, session_id): return ()


def test_create_dto_is_strict_immutable_and_forbids_server_owned_fields():
    artifact_id = uuid4()
    value = CreateImageSolvingSessionRequest(artifact_id=artifact_id)
    parsed = CreateImageSolvingSessionRequest(artifact_id=str(artifact_id))
    assert parsed.artifact_id == artifact_id
    with pytest.raises(ValidationError): CreateImageSolvingSessionRequest(artifact_id="not-a-uuid")
    with pytest.raises(ValidationError): CreateImageSolvingSessionRequest(
        artifact_id=artifact_id, owner_id=uuid4())
    with pytest.raises(ValidationError): value.artifact_id = uuid4()


async def test_foreign_session_is_hidden_as_not_found():
    service = ImageSolvingApplicationService(Flow(state()), Attempts())
    with pytest.raises(ImageSolvingApiError) as error:
        await service.state(service.flow.value.session_id, uuid4())
    assert (error.value.code, error.value.status) == ("image_solving_session_not_found", 404)


async def test_result_maps_only_public_semantic_fields():
    value = state(complete=True)
    result = await ImageSolvingApplicationService(Flow(value), Attempts()).result(
        value.session_id, value.owner_id)
    payload = result.model_dump(mode="json")
    assert payload["solution"]["answer"] == "4"
    assert payload["extraction"]["task_classification"]["task_type"] == "calculation"
    serialized = result.model_dump_json()
    for secret in ("storage_reference", "binary", "prompt", "api_key", "raw_response",
                   "provider_payload", "raw_reasoning"):
        assert secret not in serialized


async def test_result_is_not_ready_before_all_checkpoints():
    value = state()
    service = ImageSolvingApplicationService(Flow(value), Attempts())
    with pytest.raises(ImageSolvingApiError) as error:
        await service.result(value.session_id, value.owner_id)
    assert (error.value.code, error.value.status) == ("image_solving_not_ready", 409)


async def test_corrupt_checkpoint_maps_to_existing_422_response():
    value = state()

    class CorruptCheckpointFlow(Flow):
        async def get_state(self, **kwargs):
            raise ImageSolvingError("invalid_checkpoint")

    service = ImageSolvingApplicationService(CorruptCheckpointFlow(value), Attempts())
    with pytest.raises(ImageSolvingApiError) as error:
        await service.state(value.session_id, value.owner_id)
    assert (error.value.code, error.value.status) == ("invalid_artifact_or_checkpoint", 422)
