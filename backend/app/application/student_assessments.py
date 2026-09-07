"""Student attempt lifecycle primitives and conservative answer normalization."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

from app.application.assessments import AssessmentError
from app.application.answer_validation import normalize_answer


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
