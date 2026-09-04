"""Shared, bounded resolution of curriculum catalog replacement chains."""
from uuid import UUID

MAX_CATALOG_REPLACEMENT_DEPTH = 32
LIVE_CATALOG_STATUSES = ("active", "provisional")


async def resolve_effective_catalog_target(db, model, target_id: UUID | None):
    """Return the live end of a replacement chain, or ``None`` if malformed/dead."""
    current_id = target_id
    seen: set[UUID] = set()
    for _ in range(MAX_CATALOG_REPLACEMENT_DEPTH):
        if current_id is None or current_id in seen:
            return None
        seen.add(current_id)
        row = await db.get(model, current_id)
        if row is None:
            return None
        if row.status in LIVE_CATALOG_STATUSES:
            return row
        if row.status != "deprecated":
            return None
        current_id = row.replacement_id
    return None
