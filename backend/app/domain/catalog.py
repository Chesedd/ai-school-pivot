"""Lifecycle primitives shared by canonical curriculum catalog entities."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class CatalogLifecycle(StrEnum):
    PROVISIONAL = "provisional"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


@dataclass(frozen=True, slots=True)
class CatalogLifecycleState:
    status: CatalogLifecycle
    proposed_by: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CatalogLifecycle):
            raise TypeError("status must be a CatalogLifecycle")
        if self.status is CatalogLifecycle.PROVISIONAL and self.proposed_by is None:
            raise ValueError("provisional catalog entities require a proposer")
