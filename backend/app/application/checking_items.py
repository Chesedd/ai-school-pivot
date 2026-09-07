"""Source-aware item identity used only inside the shared Checking engine."""
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping
from uuid import UUID

class InvalidCheckingItemIdentity(ValueError):
    pass


class CheckingItemKind(StrEnum):
    ASSESSMENT = "assessment"
    REMEDIATION = "remediation"


@dataclass(frozen=True)
class CheckingItemKey:
    kind: CheckingItemKind
    item_id: UUID

    @property
    def columns(self) -> dict[str, UUID | None]:
        return {
            "assessment_item_id": self.item_id if self.kind is CheckingItemKind.ASSESSMENT else None,
            "remediation_plan_item_id": self.item_id if self.kind is CheckingItemKind.REMEDIATION else None,
        }


def snapshot_item_key(item: Mapping[str, Any]) -> CheckingItemKey:
    """Return the snapshot's sole source identity, rejecting both or neither."""
    assessment = item.get("assessment_item_id")
    remediation = item.get("remediation_plan_item_id")
    if (assessment is None) == (remediation is None):
        raise InvalidCheckingItemIdentity("snapshot item must have exactly one execution identity")
    try:
        return CheckingItemKey(
            CheckingItemKind.ASSESSMENT if assessment is not None else CheckingItemKind.REMEDIATION,
            UUID(str(assessment if assessment is not None else remediation)),
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidCheckingItemIdentity("invalid snapshot item identity") from exc


def find_snapshot_item(snapshot: Mapping[str, Any], key: CheckingItemKey) -> Mapping[str, Any]:
    matches = [item for item in snapshot.get("items", ()) if snapshot_item_key(item) == key]
    if len(matches) != 1:
        raise InvalidCheckingItemIdentity("execution item is absent or duplicated in snapshot")
    return matches[0]
