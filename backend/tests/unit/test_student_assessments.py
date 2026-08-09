from dataclasses import dataclass
from uuid import UUID
import pytest

from app.application.assessments import AssessmentError
from app.application.student_assessments import normalize_answer, select_deterministic_variant, validate_idempotency_key

@dataclass(frozen=True)
class Variant:
    id: UUID
    position: int

def test_deterministic_variant_exact_vector_and_canonical_order():
    assignment=UUID("00000000-0000-0000-0000-000000000001")
    student=UUID("00000000-0000-0000-0000-000000000002")
    variants=[Variant(UUID("00000000-0000-0000-0000-0000000000c0"),2),
              Variant(UUID("00000000-0000-0000-0000-0000000000b0"),1),
              Variant(UUID("00000000-0000-0000-0000-0000000000a0"),1)]
    # sha256(assignment.bytes + student.bytes) starts 0x78d68721debb423c...; unsigned BE modulo 3 == 2.
    assert select_deterministic_variant(assignment,student,variants).id==variants[0].id
    assert select_deterministic_variant(assignment,student,list(reversed(variants))).id==variants[0].id

def test_deterministic_variant_one():
    only=Variant(UUID(int=9),7)
    assert select_deterministic_variant(UUID(int=1),UUID(int=2),[only]) is only

@pytest.mark.parametrize("value",[None,""," key","key ","a/b","é","x"*129])
def test_invalid_idempotency_keys(value):
    with pytest.raises(AssessmentError) as error: validate_idempotency_key(value)
    assert error.value.code=="invalid_request"

def test_basic_normalization_contract():
    assert normalize_answer("single_choice","A")=={"option_id":"A"}
    with pytest.raises(AssessmentError): normalize_answer("single_choice"," A")
    assert normalize_answer("multiple_choice",["b","a"])=={"option_ids":["a","b"]}
    with pytest.raises(AssessmentError): normalize_answer("multiple_choice",["a","a"])
    assert normalize_answer("short_text","  Café  ")=={"text":"Café"}
    assert normalize_answer("number"," -0.00e9 ")=={"decimal":"0"}
    assert normalize_answer("number","1,20e2")=={"decimal":"120"}
    assert normalize_answer("expression"," x + X ")=={"expression":"x + X"}
    assert normalize_answer("long_text"," a\r\nb\r ")=={"text":" a\nb\n "}
