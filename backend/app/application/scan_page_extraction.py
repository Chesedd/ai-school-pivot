"""HTTP-independent contracts and summaries for scan page extraction."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ExtractionCounts:
    completed: int = 0
    failed_retryable: int = 0
    failed_terminal: int = 0


@dataclass(frozen=True, slots=True)
class ExtractionSummary:
    batch_id: UUID
    status: str
    artifacts: ExtractionCounts
    pages: dict[str, int]
