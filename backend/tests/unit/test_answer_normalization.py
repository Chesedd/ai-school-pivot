"""Golden matrix for the conservative answer normalization contract."""
import json

import pytest

from app.application.assessments import AssessmentError
from app.application.student_assessments import normalize_answer


@pytest.mark.parametrize(("fmt", "raw", "expected"), [
    ("single_choice", "Ä", {"option_id": "Ä"}),
    ("multiple_choice", ["b", "a", "A"], {"option_ids": ["A", "a", "b"]}),
    ("multiple_choice", [], {"option_ids": []}),
    ("short_text", "  Cafe\u0301  two  spaces\tX\r\nY\rZ  ", {"text": "Café  two  spaces\tX\nY\nZ"}),
    ("expression", "  x + X\r\nx−1  ", {"expression": "x + X\nx−1"}),
    ("long_text", " Cafe\u0301\r\n\rbody\t ", {"text": " Café\n\nbody\t "}),
    ("number", "0", {"decimal": "0"}), ("number", "-0", {"decimal": "0"}),
    ("number", "+0", {"decimal": "0"}), ("number", "00042", {"decimal": "42"}),
    ("number", "001.2300", {"decimal": "1.23"}), ("number", "001,2300", {"decimal": "1.23"}),
    ("number", "1e3", {"decimal": "1000"}), ("number", "1E-3", {"decimal": "0.001"}),
    ("number", "-1,2e+3", {"decimal": "-1200"}), ("number", "1.2300e2", {"decimal": "123"}),
    ("number", "12345678901234567890.12345678901234567890", {"decimal": "12345678901234567890.1234567890123456789"}),
    ("number", "1e-40", {"decimal": "0.0000000000000000000000000000000000000001"}),
])
def test_valid_golden_matrix(fmt, raw, expected):
    before = json.dumps(raw, ensure_ascii=False)
    assert normalize_answer(fmt, raw) == expected
    assert json.dumps(raw, ensure_ascii=False) == before


@pytest.mark.parametrize("raw", [None, 1, [], "", " ", "a ", " a", "x" * 201])
def test_single_choice_invalid(raw):
    assert_invalid("single_choice", raw)


@pytest.mark.parametrize("raw", [None, "a", ["a", "a"], [""], [" a"], [1], ["x" * 201], [str(x) for x in range(101)]])
def test_multiple_choice_invalid(raw):
    assert_invalid("multiple_choice", raw)


@pytest.mark.parametrize("fmt", ["short_text", "expression", "long_text"])
def test_text_code_point_boundaries(fmt):
    assert normalize_answer(fmt, "x" * 60000)
    assert_invalid(fmt, "x" * 60001)


def test_choice_boundaries():
    assert normalize_answer("single_choice", "x" * 200)
    values = [f"id{x}" for x in range(100)]
    assert len(normalize_answer("multiple_choice", values)["option_ids"]) == 100


@pytest.mark.parametrize("raw", ["", " ", "+", "-", "NaN", "Infinity", "-Infinity", "inf",
    "1 000", "1_000", "1,2.3", "1.2,3", "1e", "1e+", "1e-", "12kg", "12 kg", "0x10",
    1, 1.2, None, [], {}, ".5", "1."])
def test_invalid_number_matrix(raw):
    assert_invalid("number", raw)


def test_global_compact_utf8_boundary_and_multibyte_measurement():
    raw = "я" * 32767  # quotes add two bytes: exactly 65,536 UTF-8 bytes
    assert len(raw) == 32767
    assert len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()) == 65536
    assert normalize_answer("short_text", raw) == {"text": raw}
    assert_invalid("short_text", raw + "я")


def test_unsupported_format():
    assert_invalid("future_format", "value")


def assert_invalid(fmt, raw):
    with pytest.raises(AssessmentError) as caught:
        normalize_answer(fmt, raw)
    assert caught.value.code == "answer_format_invalid" and caught.value.status == 422
