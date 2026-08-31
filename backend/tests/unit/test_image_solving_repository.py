"""Checkpoint persistence-boundary regression tests."""
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.image_solving_contracts import (
    ExtractionResultV1, ImageSolvingStatus, SolutionResultV1, ValidationResultV1,
)
from app.infrastructure.image_solving_repository import (
    SqlAlchemyImageSolvingRepository, _deserialize_checkpoint,
)
from app.application.image_solving_promotion import validate_persisted_checkpoints

EXTRACTION = ExtractionResultV1(
    extracted_text="7 · (X - 3) = 21",
    structured_statement="Решить уравнение: 7 · (X - 3) = 21. Найти X.",
    detected_task_type="solve_linear_equation", detected_answer_format="integer",
    choices=("X = 6", "X = 0", "X = 3", "X = 24"),
    extraction_confidence=Decimal("0.95"), ocr_issues=(),
    metadata={"title":"Решение линейного уравнения","subject":"Математика",
        "grade":6,"topic":"Уравнения","subtopic":"Линейные уравнения",
        "skills":("Решать линейные уравнения",),"task_type":"calculation",
        "answer_format":"number","difficulty":2,"tags":()},
)
SOLUTION = SolutionResultV1(
    status="solved", reasoning_summary="Divide by 7, then add 3.",
    final_answer="X = 6", confidence=Decimal("0.97"),
)
VALIDATION = ValidationResultV1(
    validation_status="validated", confidence=Decimal("0.95"), findings=(),
    requires_human_review=False, extraction_confidence_check=True,
    OCR_quality_check=True, solver_status_check=True, answer_consistency_check=True,
)


@pytest.mark.parametrize("original", [EXTRACTION, SOLUTION, VALIDATION])
def test_strict_checkpoint_roundtrips_through_jsonb_representation(original):
    persisted = original.model_dump(mode="json")
    restored = _deserialize_checkpoint(persisted, type(original))

    assert restored == original
    assert restored.fingerprint == original.fingerprint


def test_promotion_uses_the_same_strict_jsonb_roundtrip_and_provenance():
    # model_dump(mode="json") is the exact dict/list/string representation
    # returned after PostgreSQL JSONB persistence, including Decimal strings.
    values = {"extraction": EXTRACTION, "solver": SOLUTION,
        "validation": VALIDATION}
    rows = {name: SimpleNamespace(payload=value.model_dump(mode="json"),
        fingerprint=value.fingerprint) for name, value in values.items()}
    restored = validate_persisted_checkpoints(rows)
    assert restored == values
    assert restored["validation"].findings == ()  # findings remain advisory


@pytest.mark.parametrize("mutation", [
    lambda payload: payload.update(unknown_field="corrupt"),
    lambda payload: payload.update(extraction_confidence="not-a-decimal"),
    lambda payload: payload.pop("structured_statement"),
])
def test_malformed_extraction_checkpoint_fails_closed(mutation):
    persisted = EXTRACTION.model_dump(mode="json")
    mutation(persisted)

    with pytest.raises(ValueError, match="invalid_checkpoint"):
        _deserialize_checkpoint(persisted, ExtractionResultV1)


async def test_changed_payload_with_old_fingerprint_fails_closed():
    payload = EXTRACTION.model_dump(mode="json")
    payload["extracted_text"] = "tampered"
    session_id = uuid4()
    now = datetime.now(UTC)
    session_row = SimpleNamespace(id=session_id, owner_id=uuid4(), input_artifact_id=uuid4(),
        status=ImageSolvingStatus.EXTRACTED.value, created_at=now, updated_at=now)
    checkpoint_row = SimpleNamespace(stage="extraction", payload=payload,
        fingerprint=EXTRACTION.fingerprint)
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [checkpoint_row]))
    db = SimpleNamespace(get=AsyncMock(return_value=session_row),
        execute=AsyncMock(return_value=result))

    with pytest.raises(ValueError, match="invalid_checkpoint"):
        await SqlAlchemyImageSolvingRepository(db).get(session_id)
