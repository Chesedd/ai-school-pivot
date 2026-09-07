"""Shared student answer-format validation and canonical normalization."""
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from app.application.assessments import AssessmentError


def _invalid(message: str = "Ответ не соответствует answer_format."):
    raise AssessmentError("answer_format_invalid", message, 422,
                          [{"field": "raw_answer", "code": "invalid_format", "message": message}])


def normalize_answer(answer_format: str, raw):
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 65536:
        _invalid("JSON-ответ превышает 64 KiB.")
    if answer_format == "single_choice":
        if not isinstance(raw, str) or not 1 <= len(raw) <= 200 or raw != raw.strip():
            _invalid()
        return {"option_id": raw}
    if answer_format == "multiple_choice":
        if not isinstance(raw, list) or len(raw) > 100:
            _invalid()
        if any(not isinstance(x, str) or not 1 <= len(x) <= 200 or x != x.strip() for x in raw):
            _invalid()
        if len(set(raw)) != len(raw):
            _invalid("Option IDs не должны повторяться.")
        return {"option_ids": sorted(raw)}
    if answer_format in {"short_text", "expression", "long_text"}:
        if not isinstance(raw, str) or len(raw) > 60000:
            _invalid()
        value = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
        if answer_format == "long_text":
            return {"text": value}
        value = value.strip()
        return {"text" if answer_format == "short_text" else "expression": value}
    if answer_format == "number":
        if not isinstance(raw, str):
            _invalid()
        value = raw.strip()
        if re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?", value, re.ASCII) is None:
            _invalid()
        try:
            number = Decimal(value.replace(",", "."))
        except InvalidOperation:
            _invalid()
        if not number.is_finite():
            _invalid()
        canonical = "0" if number == 0 else format(number, "f")
        if "." in canonical:
            canonical = canonical.rstrip("0").rstrip(".")
        return {"decimal": canonical}
    _invalid("Неподдерживаемый answer_format.")
