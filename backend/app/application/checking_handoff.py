"""Internal, immutable Assessment -> future Checking Engine read model."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


class CheckingHandoffNotReady(Exception):
    """The requested submission has not been submitted yet."""


@dataclass(frozen=True)
class CheckingHandoffItem:
    assessment_item_id: UUID
    task_version_id: UUID
    position: int
    points: Decimal
    answer_format: str
    raw_answer: Any | None
    normalized_answer: Any | None

    def as_dict(self) -> dict[str, Any]:
        return {"assessment_item_id": str(self.assessment_item_id),
                "task_version_id": str(self.task_version_id), "position": self.position,
                "points": format(self.points, ".2f"), "answer_format": self.answer_format,
                "raw_answer": self.raw_answer, "normalized_answer": self.normalized_answer}


@dataclass(frozen=True)
class CheckingHandoff:
    submission_id: UUID
    submitted_at: datetime
    items: tuple[CheckingHandoffItem, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"submission_id": str(self.submission_id),
                "submitted_at": self.submitted_at.isoformat().replace("+00:00", "Z"),
                "items": [item.as_dict() for item in self.items]}
