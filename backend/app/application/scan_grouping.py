"""Teacher grouping use cases; AI evidence is read-only and checking is not invoked."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class ScanGroupingError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PaperSubmissionRecord:
    id: UUID
    batch_id: UUID
    grouping_revision_id: UUID
    assignment_id: UUID
    assignment_participant_id: UUID
    student_id: UUID
    assigned_variant_id: UUID
    status: str
    created_at: datetime
    created_by_user_id: UUID


@dataclass(frozen=True)
class GroupingSeedPage:
    page_id: UUID
    source_order: int
    disposition: str
    proposed_assignment_participant_id: UUID | None
    proposed_page_order: int | None


def seed_grouping_pages(
    pages: list[GroupingSeedPage], valid_participant_ids: set[UUID]
) -> tuple[dict[UUID, list[UUID]], list[tuple[UUID, str]]]:
    """Build a safe participant-based seed without trusting AI group tokens."""
    grouped: dict[UUID, list[GroupingSeedPage]] = {}
    unresolved: list[tuple[UUID, str]] = []
    for page in sorted(pages, key=lambda item: item.source_order):
        participant_id = page.proposed_assignment_participant_id
        if page.disposition == "matched" and participant_id in valid_participant_ids:
            grouped.setdefault(participant_id, []).append(page)
        else:
            reason = (
                page.disposition
                if page.disposition in {"ambiguous", "unmatched"}
                else "invalid_match"
            )
            unresolved.append((page.page_id, reason))
    result: dict[UUID, list[UUID]] = {}
    for participant_id, participant_pages in grouped.items():
        proposed = [page.proposed_page_order for page in participant_pages]
        if all(value is not None for value in proposed) and len(set(proposed)) == len(
            proposed
        ):
            participant_pages.sort(
                key=lambda page: (page.proposed_page_order, page.source_order)
            )
        result[participant_id] = [page.page_id for page in participant_pages]
    return result, unresolved


class ScanGroupingService:
    def __init__(self, repository):
        self.repo = repository

    async def _call(self, name, *args):
        try:
            return await getattr(self.repo, name)(*args)
        except RuntimeError as exc:
            raise ScanGroupingError(exc.code) from None

    async def initialize(self, batch_id, actor_id):
        return await self._call("initialize", batch_id, actor_id)

    async def read(self, batch_id):
        return await self._call("read", batch_id)

    async def assign(
        self, batch_id, page_id, participant_id, revision, version, actor_id
    ):
        return await self._call(
            "assign", batch_id, page_id, participant_id, revision, version, actor_id
        )

    async def reorder(
        self, batch_id, participant_id, page_ids, revision, version, actor_id
    ):
        return await self._call(
            "reorder", batch_id, participant_id, page_ids, revision, version, actor_id
        )

    async def confirm(self, batch_id, revision, version, actor_id):
        return await self._call("confirm", batch_id, revision, version, actor_id)
