"""Pure, deterministic routing contracts for a materialized Checking input v1."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

SNAPSHOT_SCHEMA_VERSION = "checking_input_v1"
HANDOFF_VERSION = 1
ROUTING_CONTRACT_VERSION = "checking_routing_contract_v1"


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
    outcome: CheckerOutcome
    checker_type: CheckerType
    checker_version: str
    findings: tuple[Mapping[str, Any], ...] = ()


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
        seen=set()
        for answer in answers:
            if (answer.get("normalization_policy_code"),answer.get("normalization_policy_version")) != ("decimal_v1",1):
                return _manual(item,candidate,RoutingReason.UNSUPPORTED_NORMALIZATION)
            value=_decimal(answer.get("canonical_decimal"))
            if value is None: return _insufficient(item,candidate,RoutingReason.MISSING_CANONICAL_VALUE)
            if _decimal(answer.get("absolute_tolerance"),nonnegative=True) is None or _decimal(answer.get("relative_tolerance"),nonnegative=True) is None:
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
            if not isinstance(option,Mapping) or _uuid(option.get("id")) is None or option.get("id") in ids or option.get("option_key") in keys or option.get("order_index") in orders:
                return _insufficient(item,candidate,RoutingReason.DUPLICATE_CHOICE_OPTION)
            ids.add(option["id"]);keys.add(option.get("option_key"));orders.add(option.get("order_index"))
        accepted=set()
        for answer in answers:
            selected=answer.get("option_ids")
            if not isinstance(selected,Sequence) or isinstance(selected,(str,bytes)) or not selected or any(x not in ids for x in selected):
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
        if mode=="per_option" and (not isinstance(rules,Sequence) or any(not isinstance(r,Mapping) or r.get("option_id") not in ids for r in rules)):
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
