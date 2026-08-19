"""Pure deterministic exact/choice execution over a frozen Checking snapshot item."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any
from uuid import UUID

from app.application.checking_routing import (
    ROUTING_CONTRACT_VERSION, Checker, CheckerOutcome, CheckerRequest,
    CheckerResultDraft, CheckerType, ResultReason, RoutingDecision,
    RoutingDisposition,
)

RESULT_SCHEMA_VERSION = "1.0"
EXACT_CHECKER_VERSION = "exact_v1"
CHOICE_CHECKER_VERSION = "choice_v1"
NUMERIC_CHECKER_VERSION = "numeric_v1"
UNANSWERED_CHECKER_VERSION = "unanswered_v1"
ROUTING_FALLBACK_VERSION = "routing_fallback_v1"


class DeterministicExecutionError(ValueError):
    """Typed execution failure whose text never includes input content."""
    def __init__(self, code: str):
        if not isinstance(code, str) or not code.strip() or len(code) > 64:
            code = "invalid_execution_error"
        self.code = code
        super().__init__(code)


class FrozenEvidence(Mapping[str, Any]):
    """Small deterministic mapping used to expose evidence without mutable aliases."""
    __slots__ = ("_items",)

    def __init__(self, items: tuple[tuple[str, Any], ...]): self._items = items
    def __getitem__(self, key: str) -> Any:
        for candidate, value in self._items:
            if candidate == key: return value
        raise KeyError(key)
    def __iter__(self): return (key for key, _ in self._items)
    def __len__(self) -> int: return len(self._items)
    def __repr__(self) -> str: return f"FrozenEvidence({dict(self._items)!r})"


def _uuid(value: Any) -> str | None:
    if not isinstance(value, str): return None
    try: parsed = UUID(value)
    except (ValueError, AttributeError): return None
    return value if value == str(parsed) else None


def _points(item: Mapping[str, Any]) -> Decimal:
    value = item.get("points")
    if not isinstance(value, str): raise DeterministicExecutionError("invalid_frozen_points")
    try: result = Decimal(value)
    except InvalidOperation as exc: raise DeterministicExecutionError("invalid_frozen_points") from exc
    if not result.is_finite() or result <= 0 or result.as_tuple().exponent < -2:
        raise DeterministicExecutionError("invalid_frozen_points")
    return result


def _plain(value: Decimal) -> str:
    plain = format(value, "f")
    return "0" if value.is_zero() else plain


def _canonical_decimal(value: Any) -> Decimal | None:
    if not isinstance(value, str): return None
    try: result = Decimal(value)
    except InvalidOperation: return None
    if not result.is_finite(): return None
    plain = format(result, "f")
    if "." in plain: plain = plain.rstrip("0").rstrip(".")
    if result.is_zero(): plain = "0"
    return result if value == plain else None


def _numeric_precision(*values: Decimal) -> int:
    # Covers coefficient products and alignment across all plain-decimal operands.
    return max(64, sum(len(v.as_tuple().digits) for v in values) +
               max((abs(v.as_tuple().exponent) for v in values), default=0) + 16)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping): return FrozenEvidence(tuple(sorted((str(k), _freeze(v)) for k, v in value.items())))
    if isinstance(value, (list, tuple)): return tuple(_freeze(v) for v in value)
    if isinstance(value, float): raise DeterministicExecutionError("non_json_safe_result")
    if isinstance(value, (str, int, bool, Decimal)) or value is None: return value
    raise DeterministicExecutionError("non_json_safe_result")


def _result(request: CheckerRequest, *, outcome: CheckerOutcome, checker_type: CheckerType,
            checker_version: str, reason: ResultReason, score: Decimal | None,
            confidence: Decimal = Decimal("1.0000"), summary: str,
            feedback: str | None = None, teacher: str | None = None,
            review_reason: str | None = None, evidence: Mapping[str, Any] | None = None,
            limitations: tuple[str, ...] = ()) -> CheckerResultDraft:
    return CheckerResultDraft(
        assessment_item_id=request.decision.assessment_item_id,
        task_version_id=request.decision.task_version_id,
        outcome=outcome, checker_type=checker_type, checker_version=checker_version,
        reason_code=reason, score_suggested=score, max_score=_points(request.item),
        confidence=confidence, summary=summary, student_feedback_draft=feedback,
        teacher_summary=teacher, needs_human_review=review_reason is not None,
        needs_human_review_reason=review_reason, model_limitations=limitations,
        evidence=_freeze(evidence or {}), findings=(), schema_version=RESULT_SCHEMA_VERSION,
    )


def _thaw(value: Any) -> Any:
    if isinstance(value, Decimal): return _plain(value)
    if isinstance(value, Mapping): return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, (CheckerType, CheckerOutcome, ResultReason)): return value.value
    if isinstance(value, float): raise DeterministicExecutionError("non_json_safe_result")
    return value


def result_to_json_safe(result: CheckerResultDraft) -> dict[str, Any]:
    """Return a detached canonical-JSON-compatible result without float conversion."""
    data = {
        "schema_version": result.schema_version,
        "assessment_item_id": result.assessment_item_id,
        "task_version_id": result.task_version_id,
        "outcome": result.outcome,
        "checker_type": result.checker_type,
        "checker_version": result.checker_version,
        "reason_code": result.reason_code,
        "score_suggested": result.score_suggested,
        "max_score": result.max_score,
        "confidence": result.confidence,
        "summary": result.summary,
        "student_feedback_draft": result.student_feedback_draft,
        "teacher_summary": result.teacher_summary,
        "needs_human_review": result.needs_human_review,
        "needs_human_review_reason": result.needs_human_review_reason,
        "model_limitations": result.model_limitations,
        "evidence": result.evidence,
        "findings": result.findings,
    }
    safe = _thaw(_freeze(data))
    json.dumps(safe, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return safe


def serialize_result(result: CheckerResultDraft) -> str:
    return json.dumps(result_to_json_safe(result), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def build_unanswered_result(request: CheckerRequest) -> CheckerResultDraft:
    return _result(request, outcome=CheckerOutcome.INCORRECT,
        checker_type=request.decision.checker_type, checker_version=UNANSWERED_CHECKER_VERSION,
        reason=ResultReason.UNANSWERED, score=Decimal("0.00"),
        summary="No answer was submitted.", feedback="No answer was submitted.",
        teacher="Unanswered item.", evidence={"unanswered": True})


def build_routing_fallback_result(request: CheckerRequest) -> CheckerResultDraft:
    decision = request.decision
    if decision.disposition is RoutingDisposition.INSUFFICIENT_RUBRIC:
        return _result(request, outcome=CheckerOutcome.INSUFFICIENT_RUBRIC,
            checker_type=decision.checker_type, checker_version=ROUTING_FALLBACK_VERSION,
            reason=ResultReason.ROUTING_INSUFFICIENT_RUBRIC, score=None,
            summary="The snapshotted methodology is insufficient for automatic checking.",
            teacher="Methodology review is required.", review_reason=decision.reason_code.value,
            evidence={"routing_reason": decision.reason_code.value})
    if decision.disposition is RoutingDisposition.MANUAL_REQUIRED:
        return _result(request, outcome=CheckerOutcome.MANUAL_REQUIRED,
            checker_type=decision.checker_type, checker_version=ROUTING_FALLBACK_VERSION,
            reason=ResultReason.ROUTING_MANUAL_REQUIRED, score=None,
            summary="This item requires manual checking.", teacher="Manual checking is required.",
            review_reason=decision.reason_code.value,
            evidence={"routing_reason": decision.reason_code.value})
    raise DeterministicExecutionError("invalid_routing_fallback")


def _method(item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = item.get("methodology")
    return value if isinstance(value, Mapping) else None


class ExactChecker:
    checker_type = CheckerType.EXACT
    checker_version = EXACT_CHECKER_VERSION

    async def check(self, request: CheckerRequest) -> CheckerResultDraft:
        decision, item, method = request.decision, request.item, _method(request.item)
        if decision.disposition is not RoutingDisposition.READY or decision.checker_type is not self.checker_type or item.get("answer_format") != "short_text":
            raise DeterministicExecutionError("incompatible_checker_request")
        normalized = item.get("normalized_answer")
        if not isinstance(normalized, Mapping) or set(normalized) != {"text"} or not isinstance(normalized.get("text"), str):
            return _result(request, outcome=CheckerOutcome.UNCLEAR, checker_type=self.checker_type,
                checker_version=self.checker_version, reason=ResultReason.MALFORMED_NORMALIZED_ANSWER,
                score=None, summary="The stored answer cannot be checked safely.",
                teacher="The normalized answer is malformed.", review_reason="malformed_normalized_answer")
        answers = method.get("accepted_answers") if method else None
        valid = isinstance(answers, Sequence) and not isinstance(answers, (str, bytes)) and bool(answers)
        parsed: list[tuple[str, str]] = []
        seen: set[str] = set()
        if valid:
            for answer in answers:
                if (not isinstance(answer, Mapping) or _uuid(answer.get("id")) is None or
                    answer.get("value_kind") != "text" or
                    (answer.get("normalization_policy_code"), answer.get("normalization_policy_version")) != ("exact_text_v1", 1) or
                    not isinstance(answer.get("canonical_text"), str) or not answer["canonical_text"] or
                    answer["canonical_text"] in seen):
                    valid = False; break
                seen.add(answer["canonical_text"]); parsed.append((answer["id"], answer["canonical_text"]))
        if not valid:
            return _result(request, outcome=CheckerOutcome.INSUFFICIENT_RUBRIC,
                checker_type=self.checker_type, checker_version=self.checker_version,
                reason=ResultReason.INVALID_EXACT_METHODOLOGY, score=None,
                summary="The snapshotted exact methodology is invalid.",
                teacher="Exact methodology review is required.", review_reason="invalid_exact_methodology")
        matches = sorted(identifier for identifier, text in parsed if normalized["text"] == text)
        if matches:
            return _result(request, outcome=CheckerOutcome.CORRECT, checker_type=self.checker_type,
                checker_version=self.checker_version, reason=ResultReason.EXACT_MATCH,
                score=_points(item), summary="The answer matches an accepted answer.",
                feedback="Your answer is correct.", teacher="Exact match.",
                evidence={"alternatives_checked": len(parsed), "matched_accepted_answer_id": matches[0]})
        return _result(request, outcome=CheckerOutcome.INCORRECT, checker_type=self.checker_type,
            checker_version=self.checker_version, reason=ResultReason.EXACT_MISMATCH,
            score=Decimal("0.00"), summary="The answer does not match an accepted answer.",
            feedback="Your answer does not match the expected response.", teacher="Exact mismatch.",
            evidence={"alternatives_checked": len(parsed)})


def _choice_method(method: Mapping[str, Any] | None, fmt: str):
    if method is None: return None
    options = method.get("choice_options")
    answers = method.get("accepted_answers")
    policy = method.get("choice_scoring_policy")
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)) or not options: return None
    catalogue: dict[str, str] = {}; keys=set(); orders=set()
    for option in options:
        if (not isinstance(option, Mapping) or _uuid(option.get("id")) is None or
            not isinstance(option.get("option_key"), str) or not option["option_key"].strip() or
            isinstance(option.get("order_index"), bool) or not isinstance(option.get("order_index"), int) or option["order_index"] < 0 or
            option["id"] in catalogue or option["option_key"] in keys or option["order_index"] in orders): return None
        catalogue[option["id"]]=option["option_key"]; keys.add(option["option_key"]); orders.add(option["order_index"])
    if not isinstance(answers, Sequence) or isinstance(answers, (str, bytes)) or not answers: return None
    accepted=[]; seen_sets=set(); answer_ids=set()
    for answer in answers:
        selected=answer.get("option_ids") if isinstance(answer, Mapping) else None
        if (not isinstance(answer, Mapping) or _uuid(answer.get("id")) is None or answer["id"] in answer_ids or
            answer.get("value_kind") != "choice_set" or not isinstance(selected, Sequence) or isinstance(selected,(str,bytes)) or not selected or
            any(_uuid(x) is None or x not in catalogue for x in selected) or len(set(selected)) != len(selected) or
            fmt == "single_choice" and len(selected) != 1): return None
        chosen=frozenset(selected)
        if chosen in seen_sets: return None
        seen_sets.add(chosen); answer_ids.add(answer["id"]); accepted.append((answer["id"],chosen))
    if not isinstance(policy, Mapping) or policy.get("policy_version") != 1 or policy.get("mode") not in {"all_or_nothing","per_option"}: return None
    mode=policy["mode"]; rules=policy.get("option_rules")
    if mode == "all_or_nothing":
        if rules not in (None, [], ()): return None
        return catalogue, accepted, mode, {}
    if fmt != "multiple_choice" or not isinstance(rules, Sequence) or isinstance(rules,(str,bytes)) or len(rules)!=len(catalogue): return None
    parsed={}; correct=Decimal(0); accepted_membership=set().union(*(value for _,value in accepted))
    for rule in rules:
        if not isinstance(rule, Mapping): return None
        oid=rule.get("option_id"); role=rule.get("role"); weight=rule.get("weight")
        if oid not in catalogue or oid in parsed or rule.get("option_key") != catalogue[oid] or role not in {"correct","distractor"} or not isinstance(weight,str): return None
        try: number=Decimal(weight)
        except InvalidOperation: return None
        if not number.is_finite() or role=="correct" and number<=0 or role=="distractor" and number>=0: return None
        if (role=="correct") != (oid in accepted_membership): return None
        parsed[oid]=number
        if role=="correct": correct += number
    if correct != Decimal("1.000000"): return None
    return catalogue, accepted, mode, parsed


class ChoiceChecker:
    checker_type = CheckerType.MULTIPLE_CHOICE
    checker_version = CHOICE_CHECKER_VERSION

    async def check(self, request: CheckerRequest) -> CheckerResultDraft:
        item=request.item; decision=request.decision; fmt=item.get("answer_format")
        if decision.disposition is not RoutingDisposition.READY or decision.checker_type is not self.checker_type or fmt not in {"single_choice","multiple_choice"}:
            raise DeterministicExecutionError("incompatible_checker_request")
        parsed=_choice_method(_method(item),fmt)
        if parsed is None:
            return _result(request,outcome=CheckerOutcome.INSUFFICIENT_RUBRIC,checker_type=self.checker_type,checker_version=self.checker_version,
                reason=ResultReason.INVALID_CHOICE_METHODOLOGY,score=None,summary="The snapshotted choice methodology is invalid.",
                teacher="Choice methodology review is required.",review_reason="invalid_choice_methodology")
        catalogue,accepted,mode,rules=parsed; normalized=item.get("normalized_answer")
        key="option_id" if fmt=="single_choice" else "option_ids"
        if not isinstance(normalized,Mapping) or set(normalized)!={key}:
            return self._unclear(request,ResultReason.MALFORMED_NORMALIZED_ANSWER)
        values=[normalized[key]] if fmt=="single_choice" else normalized[key]
        if (not isinstance(values,Sequence) or isinstance(values,(str,bytes)) or
            any(_uuid(x) is None for x in values) or len(set(values))!=len(values)):
            return self._unclear(request,ResultReason.MALFORMED_NORMALIZED_ANSWER)
        if any(x not in catalogue for x in values): return self._unclear(request,ResultReason.UNKNOWN_CHOICE_OPTION)
        actual=frozenset(values)
        exact=sorted(identifier for identifier,expected in accepted if actual==expected)
        reference_id,reference=min(accepted,key=lambda row:(len(actual^row[1]),row[0]))
        common={"selected_option_ids":sorted(actual),"matched_option_ids":sorted(actual&reference),
                "missing_option_ids":sorted(reference-actual),"extra_option_ids":sorted(actual-reference),
                "policy_mode":mode,"policy_version":1}
        if exact:
            common["matched_accepted_answer_id"]=exact[0]; common.update(raw_score_fraction="1",final_score=_plain(_points(item)))
            return _result(request,outcome=CheckerOutcome.CORRECT,checker_type=self.checker_type,checker_version=self.checker_version,
                reason=ResultReason.CHOICE_MATCH,score=_points(item),summary="The selected options match an accepted answer.",
                feedback="Your selection is correct.",teacher="Choice match.",evidence=common)
        if mode=="all_or_nothing":
            common.update(raw_score_fraction="0",final_score="0.00")
            return _result(request,outcome=CheckerOutcome.INCORRECT,checker_type=self.checker_type,checker_version=self.checker_version,
                reason=ResultReason.CHOICE_MISMATCH,score=Decimal("0.00"),summary="The selected options do not match an accepted answer.",
                feedback="Your selection is not correct.",teacher="Choice mismatch.",evidence=common)
        fraction=sum((rules[x] for x in actual),Decimal(0)); bounded=min(Decimal(1),max(Decimal(0),fraction))
        score=(bounded*_points(item)).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
        outcome=CheckerOutcome.CORRECT if score==_points(item) else CheckerOutcome.INCORRECT if score==Decimal("0.00") else CheckerOutcome.PARTIALLY_CORRECT
        reason=ResultReason.CHOICE_MATCH if outcome is CheckerOutcome.CORRECT else ResultReason.CHOICE_MISMATCH if outcome is CheckerOutcome.INCORRECT else ResultReason.CHOICE_PARTIAL
        common.update(raw_score_fraction=_plain(fraction),final_score=_plain(score))
        return _result(request,outcome=outcome,checker_type=self.checker_type,checker_version=self.checker_version,
            reason=reason,score=score,summary="The selection was scored by the authored per-option policy.",
            feedback="Your selection was evaluated.",teacher="Per-option choice score.",evidence=common)

    def _unclear(self,request:CheckerRequest,reason:ResultReason)->CheckerResultDraft:
        return _result(request,outcome=CheckerOutcome.UNCLEAR,checker_type=self.checker_type,checker_version=self.checker_version,
            reason=reason,score=None,summary="The stored choice answer cannot be checked safely.",teacher="Choice answer review is required.",
            review_reason=reason.value,evidence={})


class NumericChecker:
    """Decimal-only checker over the frozen Phase 4.2 normalized value."""
    checker_type = CheckerType.NUMERIC
    checker_version = NUMERIC_CHECKER_VERSION

    async def check(self, request: CheckerRequest) -> CheckerResultDraft:
        decision, item = request.decision, request.item
        if (decision.disposition is not RoutingDisposition.READY or
                decision.checker_type is not self.checker_type or
                item.get("answer_format") != "number"):
            raise DeterministicExecutionError("incompatible_checker_request")
        normalized = item.get("normalized_answer")
        actual = (_canonical_decimal(normalized.get("decimal"))
                  if isinstance(normalized, Mapping) and set(normalized) == {"decimal"} else None)
        if actual is None:
            return _result(request, outcome=CheckerOutcome.UNCLEAR,
                checker_type=self.checker_type, checker_version=self.checker_version,
                reason=ResultReason.MALFORMED_NORMALIZED_ANSWER, score=None,
                summary="The stored numeric answer cannot be checked safely.",
                teacher="Numeric answer review is required.",
                review_reason="malformed_normalized_answer")
        parsed = self._methodology(_method(item))
        if parsed is None:
            return _result(request, outcome=CheckerOutcome.INSUFFICIENT_RUBRIC,
                checker_type=self.checker_type, checker_version=self.checker_version,
                reason=ResultReason.INVALID_NUMERIC_METHODOLOGY, score=None,
                summary="The snapshotted numeric methodology is invalid.",
                teacher="Numeric methodology review is required.",
                review_reason="invalid_numeric_methodology")

        comparisons = []
        for identifier, expected, absolute, relative in parsed:
            with localcontext() as context:
                context.prec = _numeric_precision(actual, expected, absolute, relative)
                delta = abs(actual - expected)
                threshold = absolute + relative * abs(expected)
                excess = delta - threshold
            comparisons.append((identifier, absolute, relative, delta, threshold, excess))
        matches = [row for row in comparisons if row[3] <= row[4]]
        selected = min(matches, key=lambda row: (row[3], row[0])) if matches else min(
            comparisons, key=lambda row: (row[5], row[3], row[0]))
        identifier, absolute, relative, delta, threshold, _ = selected
        evidence = {"actual_decimal": _plain(actual), "alternatives_checked": len(parsed),
            "compared_accepted_answer_id": identifier, "delta": _plain(delta),
            "threshold": _plain(threshold), "absolute_tolerance": _plain(absolute),
            "relative_tolerance": _plain(relative)}
        if matches:
            evidence["matched_accepted_answer_id"] = identifier
            return _result(request, outcome=CheckerOutcome.CORRECT,
                checker_type=self.checker_type, checker_version=self.checker_version,
                reason=ResultReason.NUMERIC_MATCH, score=_points(item),
                summary="The numeric answer is within the authored tolerance.",
                feedback="Your numeric answer is correct.", teacher="Numeric tolerance match.",
                evidence=evidence)
        return _result(request, outcome=CheckerOutcome.INCORRECT,
            checker_type=self.checker_type, checker_version=self.checker_version,
            reason=ResultReason.NUMERIC_MISMATCH, score=Decimal("0.00"),
            summary="The numeric answer is outside the authored tolerance.",
            feedback="Your numeric answer is not correct.", teacher="Numeric tolerance mismatch.",
            evidence=evidence)

    @staticmethod
    def _methodology(method: Mapping[str, Any] | None):
        answers = method.get("accepted_answers") if method else None
        if not isinstance(answers, Sequence) or isinstance(answers, (str, bytes)) or not answers:
            return None
        parsed=[]; identifiers=set(); expected_values=set()
        for answer in answers:
            if not isinstance(answer, Mapping): return None
            required={"id","value_kind","canonical_decimal","absolute_tolerance","relative_tolerance",
                "unit_code","normalization_policy_code","normalization_policy_version"}
            if not required.issubset(answer): return None
            identifier=_uuid(answer.get("id")); expected=_canonical_decimal(answer.get("canonical_decimal"))
            absolute_value=answer.get("absolute_tolerance"); relative_value=answer.get("relative_tolerance")
            absolute=Decimal(0) if absolute_value is None else _canonical_decimal(absolute_value)
            relative=Decimal(0) if relative_value is None else _canonical_decimal(relative_value)
            if (identifier is None or identifier in identifiers or answer.get("value_kind") != "decimal" or
                expected is None or expected in expected_values or absolute is None or relative is None or
                absolute < 0 or relative < 0 or answer.get("unit_code") is not None or
                (answer.get("normalization_policy_code"), answer.get("normalization_policy_version")) != ("decimal_v1", 1)):
                return None
            identifiers.add(identifier); expected_values.add(expected)
            parsed.append((identifier, expected, absolute, relative))
        return parsed


def _validate_request(request: CheckerRequest) -> None:
    if not isinstance(request.item, Mapping) or not isinstance(request.decision, RoutingDecision):
        raise DeterministicExecutionError("invalid_execution_request")
    decision=request.decision
    if decision.routing_contract_version != ROUTING_CONTRACT_VERSION or _uuid(request.item.get("assessment_item_id")) != decision.assessment_item_id or _uuid(request.item.get("task_version_id")) != decision.task_version_id:
        raise DeterministicExecutionError("execution_identity_mismatch")
    expected={RoutingDisposition.READY:(False,True),RoutingDisposition.UNANSWERED:(True,False),RoutingDisposition.INSUFFICIENT_RUBRIC:(False,False),RoutingDisposition.MANUAL_REQUIRED:(False,False)}[decision.disposition]
    if (decision.unanswered,decision.execution_required)!=expected:
        raise DeterministicExecutionError("contradictory_routing_decision")
    if (decision.disposition in {RoutingDisposition.READY, RoutingDisposition.UNANSWERED} and
        (decision.checker_type is not decision.candidate_checker_type or decision.checker_type is CheckerType.MANUAL_REQUIRED)):
        raise DeterministicExecutionError("contradictory_routing_decision")
    if (decision.disposition in {RoutingDisposition.INSUFFICIENT_RUBRIC, RoutingDisposition.MANUAL_REQUIRED} and
        decision.checker_type is not CheckerType.MANUAL_REQUIRED):
        raise DeterministicExecutionError("contradictory_routing_decision")
    if decision.disposition is RoutingDisposition.UNANSWERED and (request.item.get("raw_answer") is not None or request.item.get("normalized_answer") is not None):
        raise DeterministicExecutionError("contradictory_routing_decision")
    _points(request.item)


async def execute_deterministic(request: CheckerRequest, checkers: Mapping[CheckerType, Checker] | None = None) -> CheckerResultDraft:
    _validate_request(request)
    if request.decision.disposition is RoutingDisposition.UNANSWERED: return build_unanswered_result(request)
    if request.decision.disposition in {RoutingDisposition.INSUFFICIENT_RUBRIC,RoutingDisposition.MANUAL_REQUIRED}:
        return build_routing_fallback_result(request)
    registry=checkers if checkers is not None else {CheckerType.EXACT:ExactChecker(),
        CheckerType.MULTIPLE_CHOICE:ChoiceChecker(), CheckerType.NUMERIC:NumericChecker()}
    checker=registry.get(request.decision.checker_type)
    if checker is None: raise DeterministicExecutionError("unsupported_checker_execution")
    if checker.checker_type is not request.decision.checker_type:
        raise DeterministicExecutionError("checker_registry_mismatch")
    return await checker.check(request)
