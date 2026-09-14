"""Frozen, deterministic grading policy for scanned paper checking.

The policy is an application snapshot: providers may use its rubric context, but
only application code totals accepted scores and selects a grade label.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from app.application.authoring import canonical_json_bytes

PAPER_GRADING_POLICY_VERSION = "paper_grading_policy.v1"
MAX_POLICY_TEXT = 30_000
MAX_RULE_TEXT = 2_000
MAX_RULES = 64
MAX_ITEMS = 256
MAX_GRADE_LABEL = 64

Score = Annotated[Decimal, Field(strict=True, ge=Decimal("0"))]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


def _clean(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("text_must_be_non_empty_and_trimmed")
    return value


def _sorted_unique_rules(values: tuple[str, ...]) -> tuple[str, ...]:
    if any(len(value) > MAX_RULE_TEXT for value in values):
        raise ValueError("rule_too_long")
    cleaned = tuple(_clean(value) for value in values)
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("duplicate_rule")
    # Rules are a set of simultaneously applicable constraints, not precedence.
    return tuple(sorted(cleaned))


class RubricSource(StrEnum):
    AUTHORED = "authored"
    TEACHER_OVERRIDE = "teacher_override"


class GradeBasis(StrEnum):
    POINTS = "points"
    PERCENT = "percent"


class RubricSnapshot(_FrozenContract):
    """Bounded natural-language rubric snapshot with an optional stable reference."""

    text: StrictStr = Field(min_length=1, max_length=MAX_POLICY_TEXT)
    reference: StrictStr | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("text", "reference")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return None if value is None else _clean(value)


class AssessmentItemPolicy(_FrozenContract):
    assessment_item_id: UUID
    max_score: Score
    rubric_source: Literal["authored", "teacher_override"]
    rubric: RubricSnapshot
    partial_credit_policy: StrictStr = Field(min_length=1, max_length=MAX_RULE_TEXT)
    special_rules: tuple[StrictStr, ...] = Field(default=(), max_length=MAX_RULES)

    @field_validator("max_score")
    @classmethod
    def positive_maximum(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("max_score_must_be_positive")
        return value

    @field_validator("partial_credit_policy")
    @classmethod
    def clean_policy(cls, value: str) -> str:
        return _clean(value)

    @field_validator("special_rules")
    @classmethod
    def normalize_rules(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique_rules(value)


class GradeThreshold(_FrozenContract):
    minimum: Score
    grade_label: StrictStr = Field(min_length=1, max_length=MAX_GRADE_LABEL)

    @field_validator("minimum")
    @classmethod
    def finite_minimum(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("minimum_must_be_finite")
        return value

    @field_validator("grade_label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        return _clean(value)


class GradeScale(_FrozenContract):
    basis: Literal["points", "percent"]
    thresholds: tuple[GradeThreshold, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_thresholds(self) -> "GradeScale":
        minima = [entry.minimum for entry in self.thresholds]
        if any(left <= right for left, right in zip(minima, minima[1:])):
            raise ValueError("thresholds_must_be_strictly_descending")
        if minima[-1] != Decimal("0"):
            raise ValueError("thresholds_must_cover_zero")
        labels = [entry.grade_label for entry in self.thresholds]
        if len(set(labels)) != len(labels):
            raise ValueError("grade_labels_must_be_unique")
        if self.basis == GradeBasis.PERCENT and any(value > 100 for value in minima):
            raise ValueError("percent_threshold_out_of_range")
        return self


class ScanGradingPolicy(_FrozenContract):
    schema_version: StrictStr = PAPER_GRADING_POLICY_VERSION
    original_teacher_instruction: StrictStr = Field(
        min_length=1, max_length=MAX_POLICY_TEXT
    )
    assessment_items: tuple[AssessmentItemPolicy, ...] = Field(
        min_length=1, max_length=MAX_ITEMS
    )
    total_max_score: Score
    general_rules: tuple[StrictStr, ...] = Field(default=(), max_length=MAX_RULES)
    grade_scale: GradeScale
    additional_context: StrictStr | None = Field(
        default=None, min_length=1, max_length=MAX_POLICY_TEXT
    )
    compiler_version: StrictStr = Field(min_length=1, max_length=128)
    prompt_policy_version: StrictStr = Field(min_length=1, max_length=128)

    @field_validator("schema_version")
    @classmethod
    def supported_schema(cls, value: str) -> str:
        if value != PAPER_GRADING_POLICY_VERSION:
            raise ValueError("unsupported_schema_version")
        return value

    @field_validator(
        "original_teacher_instruction",
        "additional_context",
        "compiler_version",
        "prompt_policy_version",
    )
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return None if value is None else _clean(value)

    @field_validator("general_rules")
    @classmethod
    def normalize_rules(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique_rules(value)

    @field_validator("assessment_items")
    @classmethod
    def normalize_items(
        cls, value: tuple[AssessmentItemPolicy, ...]
    ) -> tuple[AssessmentItemPolicy, ...]:
        if len({item.assessment_item_id for item in value}) != len(value):
            raise ValueError("duplicate_assessment_item")
        # Item declaration order has no grading meaning; canonicalize it.
        return tuple(sorted(value, key=lambda item: str(item.assessment_item_id)))

    @model_validator(mode="after")
    def totals_are_consistent(self) -> "ScanGradingPolicy":
        if not self.total_max_score.is_finite() or self.total_max_score <= 0:
            raise ValueError("total_max_score_must_be_positive")
        if (
            sum((item.max_score for item in self.assessment_items), Decimal("0"))
            != self.total_max_score
        ):
            raise ValueError("item_maxima_must_equal_total")
        if self.grade_scale.basis == GradeBasis.POINTS and any(
            entry.minimum > self.total_max_score
            for entry in self.grade_scale.thresholds
        ):
            raise ValueError("points_threshold_above_total")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class AcceptedItemScore(_FrozenContract):
    assessment_item_id: UUID
    score: Score

    @field_validator("score")
    @classmethod
    def finite_score(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("score_must_be_finite")
        return value


class GradeOverrideCommand(_FrozenContract):
    calculated_grade: StrictStr = Field(min_length=1, max_length=MAX_GRADE_LABEL)
    override_grade: StrictStr = Field(min_length=1, max_length=MAX_GRADE_LABEL)
    reason: StrictStr = Field(min_length=1, max_length=2_000)

    @field_validator("calculated_grade", "override_grade", "reason")
    @classmethod
    def clean_values(cls, value: str) -> str:
        return _clean(value)


def calculate_total(
    policy: ScanGradingPolicy, scores: tuple[AcceptedItemScore, ...]
) -> Decimal:
    """Validate complete accepted per-item scores and sum them without floats."""
    maxima = {
        item.assessment_item_id: item.max_score for item in policy.assessment_items
    }
    received = [entry.assessment_item_id for entry in scores]
    if len(set(received)) != len(received):
        raise ValueError("duplicate_item_score")
    if set(received) != set(maxima):
        raise ValueError("item_score_coverage_mismatch")
    if any(entry.score > maxima[entry.assessment_item_id] for entry in scores):
        raise ValueError("item_score_out_of_range")
    return sum((entry.score for entry in scores), Decimal("0"))


def calculate_grade(policy: ScanGradingPolicy, total_score: Decimal) -> str:
    """Select the first descending threshold met by points or exact percentage."""
    if (
        type(total_score) is not Decimal
        or not total_score.is_finite()
        or total_score < 0
        or total_score > policy.total_max_score
    ):
        raise ValueError("total_score_out_of_range")
    basis_value = (
        total_score
        if policy.grade_scale.basis == GradeBasis.POINTS
        else total_score * Decimal("100") / policy.total_max_score
    )
    return next(
        entry.grade_label
        for entry in policy.grade_scale.thresholds
        if basis_value >= entry.minimum
    )
