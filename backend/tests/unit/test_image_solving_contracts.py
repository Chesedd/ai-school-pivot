from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.application.authoring import AuthoringRequestV1
from app.application.authoring_pipeline import GeneratedTaskDraftV1
from app.application.image_solving_contracts import (
    MAX_USER_CONTEXT,
    ExtractionResultV1,
    InputArtifactV1,
    SolutionResultV1,
    ValidationResultV1,
)


def artifact(**changes):
    data = dict(artifact_id="upload-01", mime_type="image/png", content_hash="a" * 64,
                user_context="Solve the task shown in the image.")
    data.update(changes)
    return InputArtifactV1.model_validate(data)


def test_input_artifact_is_strict_frozen_bounded_and_closed():
    value = artifact()
    with pytest.raises(ValidationError):
        value.user_context = "changed"
    with pytest.raises(ValidationError):
        artifact(extra="not allowed")
    with pytest.raises(ValidationError):
        artifact(content_hash="A" * 64)
    with pytest.raises(ValidationError):
        artifact(user_context="x" * (MAX_USER_CONTEXT + 1))
    with pytest.raises(ValidationError):
        artifact(artifact_id=123)


def test_extraction_contract_bounds_optional_fields_and_uses_immutable_collections():
    value = ExtractionResultV1(extracted_text="What is 2 + 2?", structured_statement="Compute 2 + 2.",
        detected_task_type=None, detected_answer_format="number", choices=None,
        extraction_confidence=Decimal("0.95"), ocr_issues=("Superscript was faint.",), metadata={"title":"Сложение чисел","subject":"Математика","grade":1,"topic":"Сложение","subtopic":"Натуральные числа","skills":("Складывать числа",),"task_type":"calculation","answer_format":"number","difficulty":1,"tags":()})
    assert value.choices is None and isinstance(value.ocr_issues, tuple)
    with pytest.raises(ValidationError):
        ExtractionResultV1.model_validate({**value.model_dump(), "ocr_issues": ["mutable input"]})
    with pytest.raises(ValidationError):
        ExtractionResultV1.model_validate({**value.model_dump(), "choices": ("A", "A")})
    with pytest.raises(ValidationError):
        ExtractionResultV1.model_validate({**value.model_dump(), "extraction_confidence": Decimal("1.01")})


def test_solution_and_validation_contracts_are_strict_bounded_and_closed():
    solution = SolutionResultV1(status="solved", reasoning_summary="Addition gives four.",
        final_answer="4", confidence=Decimal("0.99"))
    validation = ValidationResultV1(validation_status="validated", confidence=Decimal("1"),
        findings=(), requires_human_review=False)
    assert solution.final_answer == "4" and validation.findings == ()
    with pytest.raises(ValidationError):
        SolutionResultV1.model_validate({**solution.model_dump(), "confidence": "0.99"})
    with pytest.raises(ValidationError):
        ValidationResultV1.model_validate({**validation.model_dump(), "requires_human_review": 0})
    with pytest.raises(ValidationError):
        ValidationResultV1.model_validate({**validation.model_dump(), "unknown": True})


def test_solution_schema_exposes_canonical_machine_status():
    assert SolutionResultV1.model_json_schema()["properties"]["status"]["const"] == "solved"


def test_fingerprints_use_deterministic_canonical_json_and_change_with_semantics():
    value = artifact()
    expected = (b'{"artifact_id":"upload-01","content_hash":"' + b"a" * 64
                + b'","mime_type":"image/png","user_context":"Solve the task shown in the image."}')
    assert value.canonical_bytes() == expected
    assert value.fingerprint == "afdccfc04cedfb210bd197ed4927dde72850f9956a037f9bebc46d7893e3e315"
    assert value.fingerprint != artifact(user_context="Return only the answer.").fingerprint


def test_legacy_authoring_contracts_remain_available_without_pipeline_changes():
    assert AuthoringRequestV1.__name__ == "AuthoringRequestV1"
    assert GeneratedTaskDraftV1.__name__ == "GeneratedTaskDraftV1"


def test_extraction_metadata_is_russian_unicode_strict_frozen_and_closed():
    value = ExtractionResultV1(extracted_text="7 · (X - 3) = 21",
        structured_statement="Решить уравнение 7 · (X - 3) = 21.",
        detected_task_type="calculation", detected_answer_format="number", choices=None,
        extraction_confidence=Decimal(".99"), ocr_issues=(), metadata={
            "title":"Решение линейного уравнения","subject":"Математика","grade":6,
            "topic":"Уравнения","subtopic":"Линейные уравнения",
            "skills":("Решать линейные уравнения",),"task_type":"calculation",
            "answer_format":"number","difficulty":2,"tags":()})
    assert value.metadata.subject == "Математика" and value.metadata.grade == 6
    with pytest.raises(ValidationError):
        value.metadata.title = "changed"
    for bad in ({"grade":"6"}, {"task_type":"invented"}, {"uuid":"not-allowed"}):
        with pytest.raises(ValidationError):
            type(value.metadata).model_validate({**value.metadata.model_dump(), **bad})
