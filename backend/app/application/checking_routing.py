"""Pure, deterministic routing contracts for a materialized Checking input v1."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

SNAPSHOT_SCHEMA_VERSION = "checking_input_v1"
HANDOFF_VERSION = 1
ROUTING_CONTRACT_VERSION = "checking_routing_contract_v1"
_EMPTY_EVIDENCE: Mapping[str, Any] = MappingProxyType({})


class CheckerType(str, Enum):
    EXACT = "exact"
    NUMERIC = "numeric"
    MULTIPLE_CHOICE = "multiple_choice"
    STRUCTURED_EXPRESSION = "structured_expression"
    LLM_RUBRIC = "llm_rubric"
    MANUAL_REQUIRED = "manual_required"


class RoutingDisposition(str, Enum):
    READY = "ready"
    UNANSWERED = "unanswered"
    INSUFFICIENT_RUBRIC = "insufficient_rubric"
    MANUAL_REQUIRED = "manual_required"


class RoutingReason(str, Enum):
    ROUTED_EXACT = "routed_exact"
    ROUTED_NUMERIC = "routed_numeric"
    ROUTED_CHOICE = "routed_choice"
    ROUTED_EXPRESSION_IDENTITY = "routed_expression_identity"
    ROUTED_OPEN_RUBRIC = "routed_open_rubric"
    UNANSWERED = "unanswered"
    MALFORMED_SNAPSHOT = "malformed_snapshot"
    MALFORMED_ITEM = "malformed_item"
    ANSWER_FORMAT_MISMATCH = "answer_format_mismatch"
    INCOMPATIBLE_TASK_FORMAT = "incompatible_task_answer_format"
    LEGACY_UNTYPED_ANSWER = "legacy_untyped_answer"
    MISSING_TYPED_ANSWER = "missing_typed_accepted_answer"
    INCOMPATIBLE_ANSWER_KIND = "incompatible_accepted_answer_kind"
    MISSING_CANONICAL_VALUE = "missing_canonical_value"
    DUPLICATE_CANONICAL = "duplicate_canonical_alternative"
    INVALID_NUMERIC_TOLERANCE = "invalid_numeric_tolerance"
    MISSING_CHOICE_OPTIONS = "missing_choice_options"
    DUPLICATE_CHOICE_OPTION = "duplicate_choice_option"
    UNKNOWN_CHOICE_OPTION = "unknown_choice_option"
    INVALID_SINGLE_CHOICE = "invalid_single_choice_cardinality"
    MISSING_CHOICE_POLICY = "missing_choice_scoring_policy"
    INVALID_WEIGHTED_POLICY = "invalid_weighted_policy"
    MISSING_EXPRESSION_IDENTITY = "missing_expression_identity_contract"
    MISSING_EXPECTED_SOLUTION = "missing_expected_solution"
    MISSING_RUBRIC = "missing_or_empty_rubric"
    RUBRIC_SCORE_MISMATCH = "rubric_max_items_mismatch"
    CONTRADICTORY_METHODOLOGY = "contradictory_methodology"
    UNKNOWN_ANSWER_FORMAT = "unknown_answer_format"
    UNSUPPORTED_CONTRACT_VERSION = "unsupported_contract_version"
    UNSUPPORTED_NORMALIZATION = "unsupported_normalization_policy"
    UNSUPPORTED_UNIT = "unsupported_unit"
    SEMANTIC_TEXT_REQUIRED = "semantic_text_judgment_required"
    EXPRESSION_EQUIVALENCE = "expression_equivalence_required"
    UNSUPPORTED_GRADING_MODE = "unsupported_grading_mode"
    OUTSIDE_V1_CAPABILITY = "outside_v1_capability"


class CheckerOutcome(str, Enum):
    CORRECT = "correct"
    PARTIALLY_CORRECT = "partially_correct"
    INCORRECT = "incorrect"
    UNCLEAR = "unclear"
    INSUFFICIENT_RUBRIC = "insufficient_rubric"
    MANUAL_REQUIRED = "manual_required"


class ResultReason(str, Enum):
    UNANSWERED = "unanswered"
    EXACT_MATCH = "exact_match"
    EXACT_MISMATCH = "exact_mismatch"
    CHOICE_MATCH = "choice_match"
    CHOICE_MISMATCH = "choice_mismatch"
    CHOICE_PARTIAL = "choice_partial"
    UNKNOWN_CHOICE_OPTION = "unknown_choice_option"
    MALFORMED_NORMALIZED_ANSWER = "malformed_normalized_answer"
    INVALID_EXACT_METHODOLOGY = "invalid_exact_methodology"
    INVALID_CHOICE_METHODOLOGY = "invalid_choice_methodology"
    NUMERIC_MATCH = "numeric_match"
    NUMERIC_MISMATCH = "numeric_mismatch"
    INVALID_NUMERIC_METHODOLOGY = "invalid_numeric_methodology"
    ROUTING_INSUFFICIENT_RUBRIC = "routing_insufficient_rubric"
    ROUTING_MANUAL_REQUIRED = "routing_manual_required"


class ResultContractError(ValueError):
    """A bounded, privacy-safe structured-result validation error."""
    def __init__(self, code: str = "invalid_result_contract"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RoutingDecision:
    assessment_item_id: str
    task_version_id: str
    routing_contract_version: str
    checker_type: CheckerType
    candidate_checker_type: CheckerType
    disposition: RoutingDisposition
    reason_code: RoutingReason
    unanswered: bool
    execution_required: bool
    diagnostics: tuple[RoutingReason, ...] = ()


@dataclass(frozen=True)
class CheckerRequest:
    """Transport-neutral immutable input; payload is the already frozen item."""
    item: Mapping[str, Any]
    decision: RoutingDecision


@dataclass(frozen=True)
class CheckerResultDraft:
    assessment_item_id: str
    task_version_id: str
    outcome: CheckerOutcome
    checker_type: CheckerType
    checker_version: str
    reason_code: ResultReason
    score_suggested: Decimal | None
    max_score: Decimal
    confidence: Decimal
    summary: str
    student_feedback_draft: str | None
    teacher_summary: str | None
    needs_human_review: bool
    needs_human_review_reason: str | None
    model_limitations: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = _EMPTY_EVIDENCE
    findings: tuple[Mapping[str, Any], ...] = ()
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        try:
            UUID(self.assessment_item_id); UUID(self.task_version_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ResultContractError("invalid_result_identity") from exc
        if self.assessment_item_id != str(UUID(self.assessment_item_id)) or self.task_version_id != str(UUID(self.task_version_id)):
            raise ResultContractError("invalid_result_identity")
        if self.schema_version != "1.0" or not self.checker_version.strip() or len(self.checker_version) > 64:
            raise ResultContractError()
        if not isinstance(self.max_score, Decimal) or not self.max_score.is_finite() or self.max_score <= 0 or self.max_score.as_tuple().exponent < -2:
            raise ResultContractError("invalid_max_score")
        if not isinstance(self.confidence, Decimal) or not self.confidence.is_finite() or not Decimal(0) <= self.confidence <= Decimal(1) or self.confidence.as_tuple().exponent < -4:
            raise ResultContractError("invalid_confidence")
        if self.score_suggested is not None and (not isinstance(self.score_suggested, Decimal) or not self.score_suggested.is_finite() or self.score_suggested.as_tuple().exponent < -2):
            raise ResultContractError("invalid_score")
        expected = {
            CheckerOutcome.CORRECT: self.max_score,
            CheckerOutcome.INCORRECT: Decimal("0.00"),
        }
        if self.outcome in expected and self.score_suggested != expected[self.outcome]:
            raise ResultContractError("invalid_outcome_score")
        if self.outcome is CheckerOutcome.PARTIALLY_CORRECT and not (self.score_suggested is not None and Decimal("0.00") < self.score_suggested < self.max_score):
            raise ResultContractError("invalid_outcome_score")
        if self.outcome in {CheckerOutcome.UNCLEAR, CheckerOutcome.INSUFFICIENT_RUBRIC, CheckerOutcome.MANUAL_REQUIRED} and self.score_suggested is not None:
            raise ResultContractError("invalid_outcome_score")
        review_outcomes = {CheckerOutcome.UNCLEAR, CheckerOutcome.INSUFFICIENT_RUBRIC, CheckerOutcome.MANUAL_REQUIRED}
        if self.needs_human_review != (self.outcome in review_outcomes) or self.needs_human_review != (self.needs_human_review_reason is not None):
            raise ResultContractError("invalid_review_contract")
        if not self.summary.strip() or not isinstance(self.model_limitations, tuple) or not isinstance(self.evidence, Mapping) or not isinstance(self.findings, tuple):
            raise ResultContractError()


@runtime_checkable
class Checker(Protocol):
    checker_type: CheckerType
    checker_version: str

    async def check(self, request: CheckerRequest) -> CheckerResultDraft: ...


class RoutingInputError(ValueError):
    """Privacy-safe outer-envelope error; never includes input values."""
    def __init__(self, reason_code: RoutingReason = RoutingReason.MALFORMED_SNAPSHOT):
        self.reason_code = reason_code
        super().__init__(reason_code.value)


_NATURAL = {
    "short_text": CheckerType.EXACT, "number": CheckerType.NUMERIC,
    "single_choice": CheckerType.MULTIPLE_CHOICE, "multiple_choice": CheckerType.MULTIPLE_CHOICE,
    "expression": CheckerType.STRUCTURED_EXPRESSION, "long_text": CheckerType.LLM_RUBRIC,
}
_COMPATIBLE = {
    "test": {"single_choice", "multiple_choice"},
    "calculation": {"short_text", "number", "expression"},
    "problem": {"number", "expression", "long_text", "short_text"},
    "open_question": {"short_text", "long_text"}, "essay": {"long_text"},
}
_READY_REASON = {
    CheckerType.EXACT: RoutingReason.ROUTED_EXACT, CheckerType.NUMERIC: RoutingReason.ROUTED_NUMERIC,
    CheckerType.MULTIPLE_CHOICE: RoutingReason.ROUTED_CHOICE,
    CheckerType.STRUCTURED_EXPRESSION: RoutingReason.ROUTED_EXPRESSION_IDENTITY,
    CheckerType.LLM_RUBRIC: RoutingReason.ROUTED_OPEN_RUBRIC,
}


def _uuid(value: Any) -> str | None:
    if not isinstance(value, str): return None
    try: parsed = UUID(value)
    except (ValueError, AttributeError): return None
    return value if value == str(parsed) else None


def _decimal(value: Any, *, positive: bool = False, nonnegative: bool = False) -> Decimal | None:
    if not isinstance(value, str): return None
    try: result = Decimal(value)
    except InvalidOperation: return None
    if not result.is_finite() or positive and result <= 0 or nonnegative and result < 0: return None
    return result


def _canonical_decimal(value: Any) -> Decimal | None:
    """Parse a finite plain Decimal only when its spelling is canonical."""
    result = _decimal(value)
    if result is None or not isinstance(value, str): return None
    plain = format(result, "f")
    if "." in plain: plain = plain.rstrip("0").rstrip(".")
    if result.is_zero(): plain = "0"
    return result if value == plain else None


def _decision(item: Mapping[str, Any], candidate: CheckerType, disposition: RoutingDisposition,
              reason: RoutingReason) -> RoutingDecision:
    effective = candidate if disposition in {RoutingDisposition.READY, RoutingDisposition.UNANSWERED} else CheckerType.MANUAL_REQUIRED
    return RoutingDecision(str(item.get("assessment_item_id", "")), str(item.get("task_version_id", "")),
        ROUTING_CONTRACT_VERSION, effective, candidate, disposition, reason,
        disposition is RoutingDisposition.UNANSWERED, disposition is RoutingDisposition.READY)


def _insufficient(item: Mapping[str, Any], candidate: CheckerType, reason: RoutingReason) -> RoutingDecision:
    return _decision(item, candidate, RoutingDisposition.INSUFFICIENT_RUBRIC, reason)


def _manual(item: Mapping[str, Any], candidate: CheckerType, reason: RoutingReason) -> RoutingDecision:
    return _decision(item, candidate, RoutingDisposition.MANUAL_REQUIRED, reason)


def _rubric_reason(method: Mapping[str, Any]) -> RoutingReason | None:
    solution = method.get("expected_solution")
    if not isinstance(solution, Mapping) or not isinstance(solution.get("solution_text"), str) or not solution["solution_text"].strip():
        return RoutingReason.MISSING_EXPECTED_SOLUTION
    rubric = method.get("rubric")
    if not isinstance(rubric, Mapping) or not isinstance(rubric.get("items"), Sequence) or isinstance(rubric.get("items"), (str, bytes)) or not rubric["items"]:
        return RoutingReason.MISSING_RUBRIC
    if rubric.get("grading_mode") != "points": return RoutingReason.UNSUPPORTED_GRADING_MODE
    maximum = _decimal(rubric.get("max_score"), positive=True)
    seen_ids, seen_orders, total = set(), set(), Decimal(0)
    if maximum is None: return RoutingReason.RUBRIC_SCORE_MISMATCH
    for row in rubric["items"]:
        if not isinstance(row, Mapping) or _uuid(row.get("id")) is None or row.get("id") in seen_ids or row.get("order_index") in seen_orders:
            return RoutingReason.MISSING_RUBRIC
        criterion, points = row.get("criterion"), _decimal(row.get("max_points"), positive=True)
        if not isinstance(criterion, str) or not criterion.strip() or points is None: return RoutingReason.MISSING_RUBRIC
        seen_ids.add(row["id"]); seen_orders.add(row["order_index"]); total += points
    return None if total == maximum else RoutingReason.RUBRIC_SCORE_MISMATCH


def _typed(method: Mapping[str, Any], kind: str) -> tuple[list[Mapping[str, Any]], RoutingReason | None]:
    values = method.get("accepted_answers")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)): return [], RoutingReason.MALFORMED_ITEM
    if any(isinstance(x, Mapping) and x.get("value_kind") == "legacy_untyped" for x in values):
        return [], RoutingReason.LEGACY_UNTYPED_ANSWER
    typed = [x for x in values if isinstance(x, Mapping) and x.get("value_kind") != "legacy_untyped"]
    if not typed: return [], RoutingReason.MISSING_TYPED_ANSWER
    if len(typed) != len(values) or any(x.get("value_kind") != kind for x in typed):
        return [], RoutingReason.INCOMPATIBLE_ANSWER_KIND
    return typed, None


def _route_answered(item: Mapping[str, Any], method: Mapping[str, Any], fmt: str, candidate: CheckerType) -> RoutingDecision:
    if fmt == "long_text":
        if any(isinstance(x, Mapping) and x.get("value_kind") != "legacy_untyped" for x in method.get("accepted_answers", ())):
            return _insufficient(item, candidate, RoutingReason.CONTRADICTORY_METHODOLOGY)
        reason = _rubric_reason(method)
        return (_manual(item, candidate, reason) if reason is RoutingReason.UNSUPPORTED_GRADING_MODE else
                _insufficient(item, candidate, reason) if reason else _decision(item, candidate, RoutingDisposition.READY, _READY_REASON[candidate]))
    if fmt == "short_text":
        answers, reason = _typed(method, "text")
        if reason is RoutingReason.MISSING_TYPED_ANSWER:
            rubric_reason = _rubric_reason(method)
            if rubric_reason is None: return _decision(item, CheckerType.LLM_RUBRIC, RoutingDisposition.READY, RoutingReason.ROUTED_OPEN_RUBRIC)
            if rubric_reason is RoutingReason.UNSUPPORTED_GRADING_MODE: return _manual(item, CheckerType.LLM_RUBRIC, rubric_reason)
        if reason: return _insufficient(item, candidate, reason)
        seen=set()
        for answer in answers:
            policy=(answer.get("normalization_policy_code"), answer.get("normalization_policy_version"))
            if policy != ("exact_text_v1", 1): return _manual(item, candidate, RoutingReason.SEMANTIC_TEXT_REQUIRED)
            value=answer.get("canonical_text")
            if not isinstance(value, str) or not value: return _insufficient(item,candidate,RoutingReason.MISSING_CANONICAL_VALUE)
            if value in seen: return _insufficient(item,candidate,RoutingReason.DUPLICATE_CANONICAL)
            seen.add(value)
    elif fmt == "number":
        answers, reason = _typed(method, "decimal")
        if reason: return _insufficient(item,candidate,reason)
        seen=set(); answer_ids=set()
        for answer in answers:
            required={"id","value_kind","canonical_decimal","absolute_tolerance","relative_tolerance",
                "unit_code","normalization_policy_code","normalization_policy_version"}
            if not required.issubset(answer):
                return _insufficient(item,candidate,RoutingReason.MALFORMED_ITEM)
            identifier = _uuid(answer.get("id"))
            if identifier is None or identifier in answer_ids:
                return _insufficient(item,candidate,RoutingReason.MALFORMED_ITEM)
            answer_ids.add(identifier)
            if (answer.get("normalization_policy_code"),answer.get("normalization_policy_version")) != ("decimal_v1",1):
                return _manual(item,candidate,RoutingReason.UNSUPPORTED_NORMALIZATION)
            value=_canonical_decimal(answer.get("canonical_decimal"))
            if value is None: return _insufficient(item,candidate,RoutingReason.MISSING_CANONICAL_VALUE)
            if any(t is not None and _canonical_decimal(t) is None for t in
                   (answer.get("absolute_tolerance"), answer.get("relative_tolerance"))):
                return _insufficient(item,candidate,RoutingReason.INVALID_NUMERIC_TOLERANCE)
            if any(t is not None and _canonical_decimal(t) < 0 for t in
                   (answer.get("absolute_tolerance"), answer.get("relative_tolerance"))):
                return _insufficient(item,candidate,RoutingReason.INVALID_NUMERIC_TOLERANCE)
            if answer.get("unit_code") is not None: return _manual(item,candidate,RoutingReason.UNSUPPORTED_UNIT)
            if value in seen: return _insufficient(item,candidate,RoutingReason.DUPLICATE_CANONICAL)
            seen.add(value)
    elif fmt == "expression":
        answers, reason = _typed(method, "expression")
        if reason: return _insufficient(item,candidate,reason)
        seen=set()
        for answer in answers:
            policy=(answer.get("normalization_policy_code"),answer.get("normalization_policy_version"))
            if policy != ("expression_identity_v1",1): return _manual(item,candidate,RoutingReason.EXPRESSION_EQUIVALENCE)
            value=answer.get("canonical_text")
            if not isinstance(value,str) or not value: return _insufficient(item,candidate,RoutingReason.MISSING_EXPRESSION_IDENTITY)
            if value in seen: return _insufficient(item,candidate,RoutingReason.DUPLICATE_CANONICAL)
            seen.add(value)
    else:
        answers, reason = _typed(method, "choice_set")
        if reason: return _insufficient(item,candidate,reason)
        options=method.get("choice_options")
        if not isinstance(options,Sequence) or isinstance(options,(str,bytes)) or not options: return _insufficient(item,candidate,RoutingReason.MISSING_CHOICE_OPTIONS)
        ids,keys,orders=set(),set(),set()
        for option in options:
            if (not isinstance(option,Mapping) or _uuid(option.get("id")) is None or
                not isinstance(option.get("option_key"),str) or not option["option_key"].strip() or
                isinstance(option.get("order_index"),bool) or not isinstance(option.get("order_index"),int) or option["order_index"] < 0 or
                option.get("id") in ids or option.get("option_key") in keys or option.get("order_index") in orders):
                return _insufficient(item,candidate,RoutingReason.DUPLICATE_CHOICE_OPTION)
            ids.add(option["id"]);keys.add(option.get("option_key"));orders.add(option.get("order_index"))
        accepted=set()
        for answer in answers:
            selected=answer.get("option_ids")
            if (not isinstance(selected,Sequence) or isinstance(selected,(str,bytes)) or not selected or
                any(_uuid(x) is None or x not in ids for x in selected) or len(set(selected)) != len(selected) or
                _uuid(answer.get("id")) is None):
                return _insufficient(item,candidate,RoutingReason.UNKNOWN_CHOICE_OPTION)
            if fmt == "single_choice" and len(selected)!=1: return _insufficient(item,candidate,RoutingReason.INVALID_SINGLE_CHOICE)
            key=tuple(sorted(selected))
            if key in accepted: return _insufficient(item,candidate,RoutingReason.DUPLICATE_CANONICAL)
            accepted.add(key)
        policy=method.get("choice_scoring_policy")
        if not isinstance(policy,Mapping) or policy.get("policy_version") != 1: return _insufficient(item,candidate,RoutingReason.MISSING_CHOICE_POLICY)
        mode=policy.get("mode")
        if mode not in {"all_or_nothing","per_option"} or mode=="per_option" and fmt!="multiple_choice":
            return _insufficient(item,candidate,RoutingReason.INVALID_WEIGHTED_POLICY)
        rules=policy.get("option_rules",())
        if mode=="all_or_nothing" and rules not in (None,(),[]):
            return _insufficient(item,candidate,RoutingReason.INVALID_WEIGHTED_POLICY)
        if mode=="per_option":
            if not isinstance(rules,Sequence) or isinstance(rules,(str,bytes)) or len(rules)!=len(ids):
                return _insufficient(item,candidate,RoutingReason.INVALID_WEIGHTED_POLICY)
            rule_ids=set(); correct=Decimal(0); membership=set().union(*accepted)
            option_keys={x["id"]:x["option_key"] for x in options}
            for rule in rules:
                weight=_decimal(rule.get("weight")) if isinstance(rule,Mapping) else None
                role=rule.get("role") if isinstance(rule,Mapping) else None
                oid=rule.get("option_id") if isinstance(rule,Mapping) else None
                if (oid not in ids or oid in rule_ids or rule.get("option_key") != option_keys.get(oid) or
                    role not in {"correct","distractor"} or weight is None or
                    role=="correct" and weight<=0 or role=="distractor" and weight>=0 or
                    (role=="correct") != (oid in membership)):
                    return _insufficient(item,candidate,RoutingReason.INVALID_WEIGHTED_POLICY)
                rule_ids.add(oid)
                if role=="correct": correct+=weight
            if correct != Decimal("1.000000"):
                return _insufficient(item,candidate,RoutingReason.INVALID_WEIGHTED_POLICY)
    return _decision(item,candidate,RoutingDisposition.READY,_READY_REASON[candidate])


def route_item(item: Mapping[str, Any]) -> RoutingDecision:
    if not isinstance(item, Mapping): raise RoutingInputError(RoutingReason.MALFORMED_ITEM)
    fmt=item.get("answer_format"); candidate=_NATURAL.get(fmt,CheckerType.MANUAL_REQUIRED)
    if _uuid(item.get("assessment_item_id")) is None or _uuid(item.get("task_version_id")) is None or _decimal(item.get("points"),positive=True) is None:
        return _insufficient(item,candidate,RoutingReason.MALFORMED_ITEM)
    method=item.get("methodology")
    if not isinstance(method,Mapping): return _insufficient(item,candidate,RoutingReason.MALFORMED_ITEM)
    raw,normalized=item.get("raw_answer"),item.get("normalized_answer")
    if (raw is None)!=(normalized is None): return _insufficient(item,candidate,RoutingReason.MALFORMED_SNAPSHOT)
    if fmt not in _NATURAL: return _manual(item,candidate,RoutingReason.UNKNOWN_ANSWER_FORMAT)
    if method.get("answer_format") != fmt: return _insufficient(item,candidate,RoutingReason.ANSWER_FORMAT_MISMATCH)
    task_type=method.get("task_type")
    if task_type not in _COMPATIBLE or fmt not in _COMPATIBLE[task_type]: return _insufficient(item,candidate,RoutingReason.INCOMPATIBLE_TASK_FORMAT)
    if raw is None: return _decision(item,candidate,RoutingDisposition.UNANSWERED,RoutingReason.UNANSWERED)
    return _route_answered(item,method,fmt,candidate)


def route_snapshot(snapshot: Mapping[str, Any]) -> tuple[RoutingDecision, ...]:
    """Route only frozen input, preserving its item order and never mutating it."""
    if not isinstance(snapshot,Mapping): raise RoutingInputError()
    items=snapshot.get("items")
    if not isinstance(items,Sequence) or isinstance(items,(str,bytes)): raise RoutingInputError()
    versions=(snapshot.get("snapshot_schema_version"),snapshot.get("handoff_version"),snapshot.get("routing_contract_version"))
    malformed = not isinstance(versions[0],str) or not isinstance(versions[1],int) or not isinstance(versions[2],str)
    if malformed: raise RoutingInputError()
    future=versions != (SNAPSHOT_SCHEMA_VERSION,HANDOFF_VERSION,ROUTING_CONTRACT_VERSION)
    result=[]; identities=set()
    for item in items:
        if not isinstance(item,Mapping): raise RoutingInputError(RoutingReason.MALFORMED_ITEM)
        identity=(item.get("assessment_item_id"),item.get("task_version_id"))
        if identity in identities: raise RoutingInputError(RoutingReason.MALFORMED_ITEM)
        identities.add(identity)
        candidate=_NATURAL.get(item.get("answer_format"),CheckerType.MANUAL_REQUIRED)
        result.append(_manual(item,candidate,RoutingReason.UNSUPPORTED_CONTRACT_VERSION) if future else route_item(item))
    return tuple(result)
