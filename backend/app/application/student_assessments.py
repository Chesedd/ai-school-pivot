"""Student attempt lifecycle primitives and conservative answer normalization."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from app.application.assessments import AssessmentError


@dataclass(frozen=True)
class PilotStudentContext:
    student_id: UUID


def select_deterministic_variant(assignment_id: UUID, student_id: UUID, variants):
    """Select from variants canonically ordered by (position, UUID)."""
    ordered = sorted(variants, key=lambda row: (row.position, row.id))
    if not ordered:
        raise ValueError("assessment has no variants")
    value = int.from_bytes(hashlib.sha256(assignment_id.bytes + student_id.bytes).digest()[:8], "big")
    return ordered[value % len(ordered)]


def command_hash(operation: str, **path_ids: UUID) -> str:
    command = {"body": {}, "operation": operation,
               "path": {key: str(value).lower() for key, value in path_ids.items()}}
    encoded = json.dumps(command, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


_KEY = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z", re.ASCII)


def validate_idempotency_key(value: str | None) -> str:
    if value is None or _KEY.fullmatch(value) is None:
        raise AssessmentError("invalid_request", "Некорректный Idempotency-Key.", 400,
                              [{"field": "Idempotency-Key", "code": "invalid", "message": "Ожидается 1..128 символов [A-Za-z0-9._:-]."}])
    return value


def _invalid(message: str = "Ответ не соответствует answer_format."):
    raise AssessmentError("answer_format_invalid", message, 422,
                          [{"field": "raw_answer", "code": "invalid_format", "message": message}])


def normalize_answer(answer_format: str, raw):
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 65536:
        _invalid("JSON-ответ превышает 64 KiB.")
    if answer_format == "single_choice":
        if not isinstance(raw, str) or not 1 <= len(raw) <= 200 or raw != raw.strip(): _invalid()
        return {"option_id": raw}
    if answer_format == "multiple_choice":
        if not isinstance(raw, list) or len(raw) > 100: _invalid()
        if any(not isinstance(x, str) or not 1 <= len(x) <= 200 or x != x.strip() for x in raw): _invalid()
        if len(set(raw)) != len(raw): _invalid("Option IDs не должны повторяться.")
        return {"option_ids": sorted(raw)}
    if answer_format in {"short_text", "expression", "long_text"}:
        if not isinstance(raw, str) or len(raw) > 60000: _invalid()
        value = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
        if answer_format == "long_text": return {"text": value.replace("\r\n", "\n").replace("\r", "\n")}
        value = value.strip()
        return {"text" if answer_format == "short_text" else "expression": value}
    if answer_format == "number":
        if not isinstance(raw, str): _invalid()
        value = raw.strip()
        if re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?", value, re.ASCII) is None: _invalid()
        try: number = Decimal(value.replace(",", "."))
        except InvalidOperation: _invalid()
        if not number.is_finite(): _invalid()
        if number == 0: canonical = "0"
        else:
            canonical = format(number, "f")
            if "." in canonical: canonical = canonical.rstrip("0").rstrip(".")
        return {"decimal": canonical}
    _invalid("Неподдерживаемый answer_format.")
