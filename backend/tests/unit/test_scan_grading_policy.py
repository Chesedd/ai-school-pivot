from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application.scan_grading_policy import (
    AcceptedItemScore,
    AssessmentItemPolicy,
    GradeOverrideCommand,
    GradeScale,
    GradeThreshold,
    RubricSnapshot,
    ScanGradingPolicy,
    calculate_grade,
    calculate_total,
)

D = Decimal
ITEM_1 = uuid4()
ITEM_2 = uuid4()


def item(identifier=ITEM_1, maximum="10", rules=("Show work.",)):
    return AssessmentItemPolicy(
        assessment_item_id=identifier,
        max_score=D(maximum),
        rubric_source="authored",
        rubric=RubricSnapshot(text="Award points for the correct method."),
        partial_credit_policy="Award proportionally.",
        special_rules=rules,
    )


def policy(**changes):
    data = dict(
        original_teacher_instruction="Use the attached rubric exactly.",
        assessment_items=(item(), item(ITEM_2, "10", ("Accept equivalents.",))),
        total_max_score=D("20"),
        general_rules=("No negative scores.", "Use exact decimals."),
        grade_scale=GradeScale(
            basis="points",
            thresholds=(
                GradeThreshold(minimum=D("18"), grade_label="5"),
                GradeThreshold(minimum=D("14"), grade_label="4"),
                GradeThreshold(minimum=D("10"), grade_label="3"),
                GradeThreshold(minimum=D("0"), grade_label="2"),
            ),
        ),
        additional_context="Semester one.",
        compiler_version="compiler.v1",
        prompt_policy_version="prompt.v1",
    )
    data.update(changes)
    return ScanGradingPolicy.model_validate(data)


def test_total_preserves_decimal_precision_and_validates_complete_bounds():
    precise = policy(
        assessment_items=(item(ITEM_1, "0.2"), item(ITEM_2, "0.1")),
        total_max_score=D("0.3"),
        grade_scale=GradeScale(
            basis="points",
            thresholds=(GradeThreshold(minimum=D("0"), grade_label="pass"),),
        ),
    )
    scores = (
        AcceptedItemScore(assessment_item_id=ITEM_1, score=D("0.2")),
        AcceptedItemScore(assessment_item_id=ITEM_2, score=D("0.1")),
    )
    assert calculate_total(precise, scores) == D("0.3")
    with pytest.raises(ValueError):
        calculate_total(precise, scores[:1])
    with pytest.raises(ValueError):
        calculate_total(precise, (scores[0], scores[0]))
    with pytest.raises(ValueError):
        calculate_total(
            precise,
            (AcceptedItemScore(assessment_item_id=ITEM_1, score=D(".21")), scores[1]),
        )


def test_points_and_percent_grades_use_exact_threshold_boundaries():
    value = policy()
    assert calculate_grade(value, D("14")) == "4"
    assert calculate_grade(value, D("13.999")) == "3"
    percent = policy(
        grade_scale=GradeScale(
            basis="percent",
            thresholds=(
                GradeThreshold(minimum=D("90"), grade_label="excellent"),
                GradeThreshold(minimum=D("70"), grade_label="pass"),
                GradeThreshold(minimum=D("0"), grade_label="not passed"),
            ),
        )
    )
    assert calculate_grade(percent, D("18")) == "excellent"
    assert calculate_grade(percent, D("14")) == "pass"


def test_grade_scale_and_policy_reject_ambiguous_or_inconsistent_values():
    with pytest.raises(ValidationError):
        GradeScale(
            basis="points",
            thresholds=(
                GradeThreshold(minimum=D("0"), grade_label="a"),
                GradeThreshold(minimum=D("1"), grade_label="b"),
            ),
        )
    with pytest.raises(ValidationError):
        GradeScale(
            basis="points",
            thresholds=(GradeThreshold(minimum=D("1"), grade_label="a"),),
        )
    with pytest.raises(ValidationError):
        GradeScale(
            basis="percent",
            thresholds=(
                GradeThreshold(minimum=D("101"), grade_label="a"),
                GradeThreshold(minimum=D("0"), grade_label="b"),
            ),
        )
    with pytest.raises(ValidationError):
        policy(
            grade_scale=GradeScale(
                basis="points",
                thresholds=(
                    GradeThreshold(minimum=D("21"), grade_label="a"),
                    GradeThreshold(minimum=D("0"), grade_label="b"),
                ),
            )
        )
    with pytest.raises(ValidationError):
        policy(total_max_score=D("21"))


def test_policy_fingerprint_is_stable_for_unordered_rules_and_items_and_changes_semantically():
    first = policy()
    reordered = policy(
        assessment_items=tuple(reversed(first.assessment_items)),
        general_rules=tuple(reversed(first.general_rules)),
    )
    assert first.canonical_bytes() == reordered.canonical_bytes()
    assert first.fingerprint == reordered.fingerprint
    assert (
        first.fingerprint
        != policy(original_teacher_instruction="A changed instruction.").fingerprint
    )
    changed_scale = GradeScale(
        basis="points",
        thresholds=(
            GradeThreshold(minimum=D("17"), grade_label="5"),
            GradeThreshold(minimum=D("14"), grade_label="4"),
            GradeThreshold(minimum=D("10"), grade_label="3"),
            GradeThreshold(minimum=D("0"), grade_label="2"),
        ),
    )
    assert first.fingerprint != policy(grade_scale=changed_scale).fingerprint
    changed_items = (
        item(rules=("A different rule.",)),
        item(ITEM_2, "10", ("Accept equivalents.",)),
    )
    assert first.fingerprint != policy(assessment_items=changed_items).fingerprint


def test_grade_override_preserves_both_grades_and_requires_reason():
    command = GradeOverrideCommand(
        calculated_grade="4",
        override_grade="зачёт",
        reason="Teacher reviewed ambiguous handwriting.",
    )
    assert (command.calculated_grade, command.override_grade) == ("4", "зачёт")
    with pytest.raises(ValidationError):
        GradeOverrideCommand(calculated_grade="4", override_grade="5", reason=" ")
