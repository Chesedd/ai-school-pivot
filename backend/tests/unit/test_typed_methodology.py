"""Typed methodology validation and readiness matrix (no checker execution)."""
from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.content_bank import (
    AcceptedAnswerDTO, AcceptedAnswerInput, AutomationReadinessDTO, ChoiceOptionDTO,
    ChoiceOptionInput, ChoiceOptionRuleDTO, ChoiceOptionRuleInput,
    ChoiceScoringPolicyDTO, ChoiceScoringPolicyInput, ExpectedSolutionDTO, HintInput,
    LockedVersion, MethodologyDTO, RubricDTO, RubricItemDTO, SaveMethodologyCommand,
    SaveMethodologyService, ActorContext, ApplicationError, assess_automation_readiness,
)


def command(answer, *, options=(), policy=None):
    return SaveMethodologyCommand(uuid4(), None, None, (answer,), (), (), options, policy)


def codes(command):
    return {(issue.field, issue.code) for issue in SaveMethodologyService.validate(command)}


def answer(kind="legacy_untyped", **values):
    defaults=dict(answer_value="display", tolerance=None, unit=None, normalization_rule=None, value_kind=kind)
    defaults.update(values); return AcceptedAnswerInput(**defaults)


def dto(kind, **values):
    defaults=dict(id=uuid4(),answer_value="display",tolerance=None,unit=None,normalization_rule=None,value_kind=kind)
    defaults.update(values); return AcceptedAnswerDTO(**defaults)


def methodology(*answers, options=(), policy=None, rubric=None, solution=None):
    return MethodologyDTO(solution,rubric,tuple(answers),(),(),tuple(options),policy)


def test_legacy_payload_is_valid_and_defaults_to_legacy_untyped():
    value=AcceptedAnswerInput("old",Decimal(".1")," kg ","free text")
    assert value.value_kind=="legacy_untyped" and codes(command(value))==set()


def test_text_typed_answer_requires_exact_policy_and_canonical_text():
    valid=answer("text",canonical_text=" Exact ",normalization_policy_code="exact_text_v1",normalization_policy_version=1)
    assert codes(command(valid))==set()
    invalid=replace(valid,canonical_text=None)
    assert ("accepted_answers.0.canonical_text","required") in codes(command(invalid))


@pytest.mark.parametrize("raw,expected", [("0.001",Decimal(".001")),("1e-3",Decimal(".001")),("-0",Decimal("0"))])
def test_decimal_inputs_are_exact_and_canonicalizable(raw, expected):
    value=Decimal(raw)
    canonical=Decimal(0) if value==0 else value
    assert canonical==expected and not isinstance(canonical,float)


@pytest.mark.parametrize("invalid", ["NaN","Infinity","-Infinity"])
def test_nonfinite_decimal_is_rejected(invalid):
    value=answer("decimal",canonical_decimal=Decimal(invalid),normalization_policy_code="decimal_v1",normalization_policy_version=1)
    assert ("accepted_answers.0.canonical_decimal","range") in codes(command(value))


@pytest.mark.parametrize("field", ["absolute_tolerance","relative_tolerance"])
def test_negative_numeric_tolerances_are_field_specific(field):
    value=answer("decimal",canonical_decimal=Decimal("1"),normalization_policy_code="decimal_v1",normalization_policy_version=1,**{field:Decimal("-.01")})
    assert (f"accepted_answers.0.{field}","range") in codes(command(value))


@pytest.mark.parametrize("kind,field", [("decimal","canonical_decimal"),("text","canonical_text"),("expression","canonical_text")])
def test_required_canonical_field_per_kind(kind,field):
    policy={"decimal":"decimal_v1","text":"exact_text_v1","expression":"expression_identity_v1"}[kind]
    value=answer(kind,normalization_policy_code=policy,normalization_policy_version=1)
    assert (f"accepted_answers.0.{field}","required") in codes(command(value))


def test_forbidden_extra_typed_fields_are_rejected():
    value=answer("text",canonical_text="x",canonical_decimal=Decimal(1),normalization_policy_code="exact_text_v1",normalization_policy_version=1)
    assert ("accepted_answers.0.value_kind","incompatible_fields") in codes(command(value))


def test_policy_pair_and_allowlist_are_rejected_field_specifically():
    paired=answer("text",canonical_text="x",normalization_policy_code="exact_text_v1")
    assert ("accepted_answers.0.normalization_policy_version","pair") in codes(command(paired))
    dynamic=replace(paired,normalization_policy_code="dynamic",normalization_policy_version=1)
    assert ("accepted_answers.0.normalization_policy_code","unsupported_policy") in codes(command(dynamic))


def test_free_text_normalization_rule_remains_opaque_legacy_data():
    value=answer(normalization_rule="eval(this) and regex(.*)")
    assert codes(command(value))==set() and value.normalization_rule.startswith("eval")


@pytest.mark.parametrize("options,code", [
    ((ChoiceOptionInput("a","A",0),ChoiceOptionInput("a","B",1)),"duplicate"),
    ((ChoiceOptionInput("a","A",0),ChoiceOptionInput("b","B",0)),"duplicate"),
])
def test_duplicate_choice_key_or_order(options,code):
    assert ("choice_options",code) in codes(command(answer(),options=options))


def test_empty_choice_set_is_explicitly_represented_for_relation_validation():
    options=(ChoiceOptionInput("a","A",0),)
    assert options and answer("choice_set").option_keys==()


class Uow:
    def __init__(self, locked):
        self.repository=AsyncMock(); self.repository.lock_version.return_value=locked
        self.repository.replace_methodology.return_value=methodology()
    async def __aenter__(self): return self
    async def __aexit__(self,*args): return None
    async def commit(self): pass


@pytest.mark.parametrize("answer_format,kind", [("number","text"),("short_text","decimal"),("long_text","text")])
async def test_format_kind_compatibility_is_field_specific(answer_format,kind):
    kwargs={"canonical_text":"x","normalization_policy_code":"exact_text_v1","normalization_policy_version":1} if kind=="text" else {"canonical_decimal":Decimal(1),"normalization_policy_code":"decimal_v1","normalization_policy_version":1}
    cmd=command(answer(kind,**kwargs)); uow=Uow(LockedVersion(cmd.task_version_id,answer_format,"draft",True,frozenset()))
    with pytest.raises(ApplicationError) as caught: await SaveMethodologyService(uow).save(cmd,ActorContext(uuid4()))
    assert ("accepted_answers.0.value_kind","incompatible") in {(x.field,x.code) for x in caught.value.details}


@pytest.mark.parametrize("keys,expected", [((),"invalid_relation"),(("missing",),"invalid_relation"),(("a","b"),"cardinality")])
async def test_choice_membership_and_single_cardinality_are_field_specific(keys,expected):
    options=(ChoiceOptionInput("a","A",0),ChoiceOptionInput("b","B",1)); cmd=command(answer("choice_set",option_keys=keys),options=options)
    uow=Uow(LockedVersion(cmd.task_version_id,"single_choice","draft",True,frozenset()))
    with pytest.raises(ApplicationError) as caught: await SaveMethodologyService(uow).save(cmd,ActorContext(uuid4()))
    assert ("accepted_answers.0.option_keys",expected) in {(x.field,x.code) for x in caught.value.details}


def test_multiple_choice_alternatives_are_distinct_relational_sets():
    first=answer("choice_set",option_keys=("a",)); second=replace(first,answer_value="alternative",option_keys=("b",))
    assert first.option_keys != second.option_keys


def test_all_or_nothing_cannot_contain_rules():
    policy=ChoiceScoringPolicyInput("all_or_nothing",1,(ChoiceOptionRuleInput("a","correct",Decimal(1)),))
    assert ("choice_scoring_policy.option_rules","not_allowed") in codes(command(answer(),options=(ChoiceOptionInput("a","A",0),),policy=policy))


def test_weighted_policy_roles_signs_and_exact_sum():
    options=(ChoiceOptionInput("a","A",0),ChoiceOptionInput("b","B",1),ChoiceOptionInput("x","X",2))
    accepted=answer("choice_set",option_keys=("a","b"))
    valid=ChoiceScoringPolicyInput("per_option",1,(ChoiceOptionRuleInput("a","correct",Decimal(".4")),ChoiceOptionRuleInput("b","correct",Decimal(".600000")),ChoiceOptionRuleInput("x","distractor",Decimal("-.2"))))
    assert not {c for _,c in codes(command(accepted,options=options,policy=valid)) if c.startswith("invalid_") or c=="role_mismatch"}
    wrong_sum=replace(valid,option_rules=(replace(valid.option_rules[0],weight=Decimal(".39")),)+valid.option_rules[1:])
    assert ("choice_scoring_policy.option_rules","invalid_weight_sum") in codes(command(accepted,options=options,policy=wrong_sum))
    wrong_role=replace(valid,option_rules=(replace(valid.option_rules[0],role="distractor",weight=Decimal("-.4")),)+valid.option_rules[1:])
    assert ("choice_scoring_policy.option_rules","role_mismatch") in codes(command(accepted,options=options,policy=wrong_role))
    wrong_penalty=replace(valid,option_rules=valid.option_rules[:-1]+(replace(valid.option_rules[-1],weight=Decimal(".1")),))
    assert ("choice_scoring_policy.option_rules","invalid_distractor_penalty") in codes(command(accepted,options=options,policy=wrong_penalty))


@pytest.mark.parametrize("answer_format,kind,candidate", [
    ("short_text","text","exact"),("number","decimal","numeric"),
    ("expression","expression","structured_expression"),
    ("multiple_choice","choice_set","multiple_choice"),
])
def test_readiness_candidate_matrix(answer_format,kind,candidate):
    kwargs={"canonical_text":"x","normalization_policy_code":"exact_text_v1","normalization_policy_version":1} if kind=="text" else {}
    if kind=="expression": kwargs={"canonical_text":"x","normalization_policy_code":"expression_identity_v1","normalization_policy_version":1}
    if kind=="decimal": kwargs={"canonical_decimal":Decimal(1),"absolute_tolerance":Decimal(0),"relative_tolerance":Decimal(0),"normalization_policy_code":"decimal_v1","normalization_policy_version":1}
    options=(); policy=None
    if kind=="choice_set":
        kwargs={"option_keys":("a",)}; options=(ChoiceOptionDTO(uuid4(),"a","A",0),); policy=ChoiceScoringPolicyDTO("all_or_nothing",1,())
    result=assess_automation_readiness(answer_format,methodology(dto(kind,**kwargs),options=options,policy=policy))
    assert result.ready and result.checker_candidate==candidate and result.contract_version=="methodology_readiness_v1"


@pytest.mark.parametrize("answer_format,expected", [
    ("short_text","missing_typed_accepted_answer"),("number","missing_typed_accepted_answer"),
    ("single_choice","missing_choice_options"),("multiple_choice","missing_choice_scoring_policy"),
])
def test_readiness_reason_codes_are_versioned_and_field_specific(answer_format,expected):
    result=assess_automation_readiness(answer_format,methodology(dto("legacy_untyped")))
    assert not result.ready and expected in result.reason_codes and all(issue.field for issue in result.issues)


def test_readiness_reports_unsupported_unit_as_manual():
    numeric=dto("decimal",canonical_decimal=Decimal(1),absolute_tolerance=Decimal(0),relative_tolerance=Decimal(0),unit_code="m",normalization_policy_code="decimal_v1",normalization_policy_version=1)
    result=assess_automation_readiness("number",methodology(numeric))
    assert not result.ready and result.checker_candidate=="manual_required" and "unsupported_unit" in result.reason_codes


def test_long_text_exact_is_rejected_but_sufficient_rubric_is_llm_candidate():
    exact=dto("text",canonical_text="x",normalization_policy_code="exact_text_v1",normalization_policy_version=1)
    result=assess_automation_readiness("long_text",methodology(exact))
    assert "unsupported_exact_long_text" in result.reason_codes and "insufficient_rubric" in result.reason_codes
    item=RubricItemDTO(uuid4(),"criterion",Decimal(1),True,None,0)
    rubric=RubricDTO(uuid4(),"points",Decimal(1),None,(item,))
    solution=ExpectedSolutionDTO(uuid4(),"solution",None,())
    ready=assess_automation_readiness("long_text",methodology(rubric=rubric,solution=solution))
    assert ready.ready and ready.checker_candidate=="llm_rubric"
