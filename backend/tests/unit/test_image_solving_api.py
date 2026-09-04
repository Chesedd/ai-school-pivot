import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application.image_solving import ImageSolvingError
from app.application.image_solving_api import ImageSolvingApiError, ImageSolvingApplicationService
from app.application.image_solving_contracts import (ExtractionResultV1, ImageSolvingSession,
    ImageSolvingStatus, SolutionResultV1, ValidationResultV1)
from app.presentation.image_solving_schemas import (CreateImageSolvingSessionRequest,
    PromoteImageSolvingRequest)


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


def promotion_payload(**changes):
    subject_id, grade_id, topic_id, skill_id = (uuid4() for _ in range(4))
    payload = {
        "title": "Addition",
        "statement": "What is 2 + 2?",
        "task_type": "calculation",
        "answer_format": "number",
        "difficulty": 1,
        "subject_id": str(subject_id),
        "grade_id": str(grade_id),
        "topic_id": str(topic_id),
        "subtopic_id": None,
        "skill_ids": [str(skill_id)],
        "tag_ids": [],
        "solution": "Add both values.",
        "final_answer": "4",
        "review_confirmed": True,
        "review_note": None,
        "confirm_questionable": False,
    }
    payload.update(changes)
    return payload, skill_id


def test_promotion_dto_parses_json_uuid_arrays_to_uuid_tuples():
    tag_id = uuid4()
    payload, skill_id = promotion_payload(tag_ids=[str(tag_id)])

    parsed = PromoteImageSolvingRequest.model_validate(payload)

    assert parsed.skill_ids == (skill_id,)
    assert parsed.tag_ids == (tag_id,)
    assert isinstance(parsed.skill_ids, tuple)
    assert isinstance(parsed.tag_ids, tuple)
    assert all(type(value) is type(skill_id) for value in (*parsed.skill_ids, *parsed.tag_ids))


def test_promotion_dto_parses_empty_json_tag_array_to_tuple():
    payload, skill_id = promotion_payload()

    parsed = PromoteImageSolvingRequest.model_validate(payload)

    assert parsed.skill_ids == (skill_id,)
    assert parsed.tag_ids == ()


def test_promotion_dto_parses_empty_json_alias_array_to_tuple():
    payload, _ = promotion_payload(alias_confirmations=[])

    parsed = PromoteImageSolvingRequest.model_validate_json(json.dumps(payload))

    assert parsed.alias_confirmations == ()


def test_promotion_dto_parses_non_empty_json_alias_array():
    target_id = uuid4()
    payload, _ = promotion_payload(alias_confirmations=[{
        "kind": "topic",
        "recognized_label": "Линейные уравнения",
        "target_id": str(target_id),
    }])

    parsed = PromoteImageSolvingRequest.model_validate_json(json.dumps(payload))

    assert len(parsed.alias_confirmations) == 1
    assert parsed.alias_confirmations[0].kind == "topic"
    assert parsed.alias_confirmations[0].target_id == target_id


def test_promotion_dto_rejects_more_than_eight_json_aliases():
    target_id = uuid4()
    alias = {
        "kind": "topic",
        "recognized_label": "Линейные уравнения",
        "target_id": str(target_id),
    }
    payload, _ = promotion_payload(alias_confirmations=[alias] * 9)

    with pytest.raises(ValidationError):
        PromoteImageSolvingRequest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "invalid_alias",
    (
        {"kind": "folder", "recognized_label": "Label", "target_id": str(uuid4())},
        {"kind": "topic", "recognized_label": "Label", "target_id": "not-a-uuid"},
        {
            "kind": "topic",
            "recognized_label": "Label",
            "target_id": str(uuid4()),
            "unknown": "field",
        },
    ),
)
def test_promotion_dto_rejects_invalid_nested_json_alias(invalid_alias):
    payload, _ = promotion_payload(alias_confirmations=[invalid_alias])

    with pytest.raises(ValidationError):
        PromoteImageSolvingRequest.model_validate_json(json.dumps(payload))


def test_promotion_dto_rejects_invalid_uuid_in_json_array():
    payload, _ = promotion_payload(skill_ids=["not-a-uuid"])

    with pytest.raises(ValidationError):
        PromoteImageSolvingRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "error_code"),
    (("skill_ids", "duplicate_skills"), ("tag_ids", "duplicate_tags")),
)
def test_promotion_dto_rejects_duplicate_uuid_in_json_arrays(field, error_code):
    duplicate_id = uuid4()
    payload, _ = promotion_payload(**{field: [str(duplicate_id), str(duplicate_id)]})

    with pytest.raises(ValidationError, match=error_code):
        PromoteImageSolvingRequest.model_validate(payload)


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

@pytest.mark.parametrize("value", ["", "   ", " leading", "trailing ", "x" * 4001])
def test_solution_instruction_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        CreateImageSolvingSessionRequest(artifact_id=uuid4(), solution_instruction=value)


def test_solution_instruction_accepts_null_and_bounded_text():
    assert CreateImageSolvingSessionRequest(artifact_id=uuid4()).solution_instruction is None
    assert CreateImageSolvingSessionRequest(artifact_id=uuid4(), solution_instruction="Реши через дискриминант").solution_instruction == "Реши через дискриминант"
