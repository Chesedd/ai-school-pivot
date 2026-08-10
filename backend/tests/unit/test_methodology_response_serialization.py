"""Lossless JSON serialization for methodology Decimal response fields."""
import json
from decimal import Decimal
from uuid import uuid4

from app.presentation.schemas import (
    AcceptedAnswerResponse, ChoiceOptionRuleResponse, RubricItemResponse,
    RubricResponse, plain_decimal,
)


def accepted(**changes):
    values={
        "id":uuid4(),"answer_value":"display","tolerance":None,"unit":None,
        "normalization_rule":None,"value_kind":"decimal","canonical_text":None,
        "canonical_decimal":Decimal("1E-21"),"option_keys":[],"option_ids":[],
        "absolute_tolerance":Decimal("0.125"),"relative_tolerance":Decimal("-0"),
        "unit_code":None,"normalization_policy_code":"decimal_v1",
        "normalization_policy_version":1,
    }
    values.update(changes)
    return AcceptedAnswerResponse(**values)


def test_accepted_answer_decimal_json_is_plain_exact_and_optional():
    model=accepted(tolerance=Decimal("0.0100"))
    assert isinstance(model.canonical_decimal,Decimal)
    body=json.loads(model.model_dump_json())
    assert body["canonical_decimal"]=="0.000000000000000000001"
    assert body["absolute_tolerance"]=="0.125"
    assert body["relative_tolerance"]=="0"
    assert body["tolerance"]=="0.0100"
    assert accepted(canonical_decimal=None).model_dump(mode="json")["canonical_decimal"] is None


def test_scoring_weight_and_rubric_decimals_use_the_same_formatter():
    rule=ChoiceOptionRuleResponse(option_key="a",role="correct",weight=Decimal("1E-21"))
    item=RubricItemResponse(id=uuid4(),criterion="c",max_points=Decimal("2.500"),required=True,common_failure=None,order_index=0)
    rubric=RubricResponse(id=uuid4(),grading_mode="points",max_score=Decimal("-0"),notes=None,items=[item])
    assert rule.model_dump(mode="json")["weight"]=="0.000000000000000000001"
    assert rubric.model_dump(mode="json")["max_score"]=="0"
    assert rubric.model_dump(mode="json")["items"][0]["max_points"]=="2.500"


def test_plain_decimal_never_uses_a_float_precision_path():
    assert "float" not in plain_decimal.__code__.co_names
    value=Decimal("12345678901234567890.12345678901234567890")
    assert plain_decimal(value)=="12345678901234567890.12345678901234567890"
    assert plain_decimal(Decimal("-0E-100"))=="0"
    assert plain_decimal(None) is None
