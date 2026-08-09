import json
from pathlib import Path
from uuid import UUID

from app.application.checking_handoff import CheckingHandoff, CheckingHandoffItem
from app.application.student_assessments import normalize_answer


def test_checking_handoff_v1_fixture_schema_and_invariants():
    data = json.loads((Path(__file__).parents[1] / "fixtures/checking_handoff_v1.json").read_text())
    assert data["version"] == 1
    assert {case["case_id"] for case in data["cases"]} == {
        "choice.single.basic", "choice.multiple.order", "text.short.nfc_newline",
        "number.decimal_comma", "number.exponent", "expression.preserved",
        "text.long.newlines", "unanswered.basic"}
    forbidden = {"student_id", "participant_id", "display_name", "external_ref", "score",
                 "verdict", "confidence", "correct", "correctness", "awarded_points"}
    for case in data["cases"]:
        projected = {key: case[key] for key in ("submission_id", "submitted_at", "items")}
        assert not (forbidden & set(projected))
        assert list(projected) == ["submission_id", "submitted_at", "items"]
        for item in projected["items"]:
            assert set(item) == {"assessment_item_id", "task_version_id", "position", "points",
                                 "answer_format", "raw_answer", "normalized_answer"}
            assert not (forbidden & set(item))
            if item["raw_answer"] is None:
                assert item["normalized_answer"] is None
            else:
                assert normalize_answer(item["answer_format"], item["raw_answer"]) == item["normalized_answer"]


def test_application_projection_matches_documented_keys():
    item = CheckingHandoffItem(UUID(int=2), UUID(int=3), 1, __import__("decimal").Decimal("2.50"),
                               "short_text", None, None)
    handoff = CheckingHandoff(UUID(int=1), __import__("datetime").datetime(2026, 1, 1,
        tzinfo=__import__("datetime").timezone.utc), (item,)).as_dict()
    assert list(handoff) == ["submission_id", "submitted_at", "items"]
    assert set(handoff["items"][0]) == {"assessment_item_id", "task_version_id", "position",
        "points", "answer_format", "raw_answer", "normalized_answer"}
