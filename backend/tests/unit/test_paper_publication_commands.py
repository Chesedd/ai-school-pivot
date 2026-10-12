"""Command fingerprints, bounded conflicts and replay orchestration."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.application.paper_result_publication import (
    PaperPublicationError,
    PublicationEntry,
    publication_command_fingerprint,
    validate_publication_request_key,
)
from app.infrastructure.paper_checking_review_models import PaperCheckReviewRevision
from app.infrastructure.paper_result_publication_models import (
    PaperResultPublication,
    PaperResultPublicationCommand,
    PaperResultPublicationCommandItem,
    PaperResultPublicationState,
    PaperResultPublicationWithdrawal,
)
from app.infrastructure.paper_result_publication_repository import (
    PaperResultPublicationRepository,
)
from sqlalchemy.sql.dml import Insert


def entries():
    return [
        PublicationEntry(
            paper_submission_id=uuid4(), approved_review_revision_id=uuid4()
        )
        for _ in range(3)
    ]


@pytest.mark.parametrize(
    "change", ["paper", "revision", "kind", "batch", "reason", "selection"]
)
def test_fingerprint_covers_command_meaning(change):
    items, batch = entries(), uuid4()
    before = publication_command_fingerprint("selected", items, batch_id=batch)
    changed = list(items)
    kind, reason = "selected", None
    if change == "paper":
        changed[0] = items[0].model_copy(update={"paper_submission_id": uuid4()})
    if change == "revision":
        changed[0] = items[0].model_copy(
            update={"approved_review_revision_id": uuid4()}
        )
    if change == "kind":
        kind = "single"
    if change == "batch":
        batch = uuid4()
    if change == "reason":
        reason = "Correction"
    if change == "selection":
        changed.pop()
    assert before != publication_command_fingerprint(
        kind, changed, batch_id=batch, reason=reason
    )


def test_fingerprint_is_stable_and_selection_order_is_immaterial():
    items = entries()
    assert publication_command_fingerprint(
        "selected", items
    ) == publication_command_fingerprint("selected", list(reversed(items)))


@pytest.mark.parametrize("key", [None, "", " ", "\n\t", 1, "x" * 129])
def test_request_key_bounds(key):
    with pytest.raises(PaperPublicationError, match="request_key_invalid"):
        validate_publication_request_key(key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["publish", "publish_selected", "bulk", "withdraw", "withdraw_selected"]
)
@pytest.mark.parametrize(
    "later_state", ["withdrawn", "superseded", "reused_then_withdrawn"]
)
async def test_historical_replay_never_takes_paper_locks_or_writes(method, later_state):
    repo = PaperResultPublicationRepository(AsyncMock(), storage=Mock())
    historical = [
        {
            "id": uuid4(),
            "outcome": "already_current"
            if later_state == "reused_then_withdrawn"
            else "published",
        }
    ]
    repo._claim_command = AsyncMock(return_value=(uuid4(), historical))
    repo._lock_papers = AsyncMock()
    repo._publish_selected = AsyncMock()
    repo._record_command = AsyncMock()
    item, actor = entries()[0], uuid4()
    if method == "publish":
        args = (item.paper_submission_id, item.approved_review_revision_id, actor)
    elif method == "publish_selected":
        args = ([item], actor)
    elif method == "bulk":
        args = (uuid4(), actor)
    elif method == "withdraw":
        args = (item.paper_submission_id, uuid4(), "Correction", actor)
    else:
        from app.application.paper_result_publication import WithdrawalEntry

        args = (
            [
                WithdrawalEntry(
                    paper_submission_id=item.paper_submission_id, publication_id=uuid4()
                )
            ],
            "Correction",
            actor,
        )
    result = await getattr(repo, method)(*args, request_key="old-key")
    assert result == (
        historical[0] if method in {"publish", "withdraw"} else historical
    )
    repo._lock_papers.assert_not_awaited()
    repo._publish_selected.assert_not_awaited()
    repo._record_command.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["published", "already_current"])
async def test_new_key_records_created_or_reused_publication(outcome):
    repo = PaperResultPublicationRepository(AsyncMock(), storage=Mock())
    command_id, item, actor = uuid4(), entries()[0], uuid4()
    result = [{"id": uuid4(), "outcome": outcome}]
    repo._claim_command = AsyncMock(return_value=(command_id, None))
    repo._publish_selected = AsyncMock(return_value=result)
    repo._record_command = AsyncMock(return_value=result)
    assert (
        await repo.publish(
            item.paper_submission_id,
            item.approved_review_revision_id,
            actor,
            request_key="new-key",
        )
        == result[0]
    )
    repo._publish_selected.assert_awaited_once()
    repo._record_command.assert_awaited_once_with(command_id, result)


@pytest.mark.asyncio
@pytest.mark.parametrize("different", ["fingerprint", "kind"])
async def test_existing_key_rejects_mismatched_fingerprint_before_outcome_read(
    different,
):
    db = AsyncMock()
    db.scalar.side_effect = [
        None,
        SimpleNamespace(
            request_fingerprint="other" if different == "fingerprint" else "hash",
            command_kind="bulk" if different == "kind" else "single",
        ),
    ]
    repo = PaperResultPublicationRepository(db, storage=Mock())
    with pytest.raises(PaperPublicationError) as error:
        await repo._claim_command(uuid4(), "key", "single", "hash")
    assert (error.value.code, error.value.status) == (
        "paper_publication_request_key_conflict",
        409,
    )
    db.scalars.assert_not_awaited()


class MemorySession:
    """SQL session test double; exercise the production ledger and pointer code.

    Durability and database contention are separately tested on PostgreSQL.
    """

    def __init__(self):
        self.rows = {}
        self.now = datetime.now(UTC)

    def add(self, row):
        if isinstance(row, PaperResultPublicationState):
            key = row.paper_submission_id
        elif isinstance(row, PaperResultPublicationCommandItem):
            key = (row.command_id, row.position)
        else:
            key = row.id
        self.rows.setdefault(type(row), {})[key] = row

    async def get(self, model, key):
        if model is PaperCheckReviewRevision:
            return SimpleNamespace(review_id=key)
        return self.rows.get(model, {}).get(key)

    async def flush(self):
        for rows in self.rows.values():
            for row in rows.values():
                if hasattr(row, "created_at") and row.created_at is None:
                    row.created_at = self.now

    async def scalar(self, statement):
        params = statement.compile().params
        if isinstance(statement, Insert):
            existing = [
                row
                for row in self.rows.get(PaperResultPublicationCommand, {}).values()
                if row.actor_user_id == params["actor_user_id"]
                and row.request_key == params["request_key"]
            ]
            if existing:
                return None
            row = PaperResultPublicationCommand(**params)
            self.add(row)
            await self.flush()
            return row.id
        model = statement.column_descriptions[0].get("entity")
        if model is None:
            return self.now
        rows = self.rows.get(model, {}).values()
        selected = [
            row
            for row in rows
            if all(
                getattr(row, name.rsplit("_", 1)[0]) == value
                for name, value in params.items()
            )
        ]
        return selected[0] if selected else None

    async def scalars(self, statement):
        model = statement.column_descriptions[0].get("entity")
        command_id = statement.compile().params["command_id_1"]
        selected = sorted(
            (
                row
                for row in self.rows.get(model, {}).values()
                if row.command_id == command_id
            ),
            key=lambda row: row.position,
        )
        return SimpleNamespace(all=lambda: selected)


def memory_repository(db, items):
    repo = PaperResultPublicationRepository(db, storage=Mock())
    repo._lock_papers = AsyncMock(
        return_value={
            item.paper_submission_id: SimpleNamespace(id=item.paper_submission_id)
            for item in items
        }
    )
    repo._snapshot = AsyncMock(
        return_value=SimpleNamespace(model_dump=lambda **kwargs: {})
    )
    repo._available_pages = AsyncMock()
    repo.project = lambda row, active, outcome=None: {
        "id": row.id,
        "paper_submission_id": row.paper_submission_id,
        "approved_review_revision_id": row.approved_review_revision_id,
        "publication_number": row.publication_number,
        "active": active,
        "outcome": outcome,
    }
    return repo


@pytest.mark.asyncio
async def test_single_command_lifecycle_including_reuse_withdrawal_and_new_revision():
    db, actor, item = MemorySession(), uuid4(), entries()[0]
    repo = memory_repository(db, [item])

    async def publish_key(key, revision=None):
        return await repo.publish(
            item.paper_submission_id,
            revision or item.approved_review_revision_id,
            actor,
            request_key=key,
        )

    a = await publish_key("K1")
    assert await publish_key("K1") == a
    reused = await publish_key("reuse")
    assert reused["id"] == a["id"] and reused["outcome"] == "already_current"
    await repo.withdraw(
        item.paper_submission_id, a["id"], "Correction", actor, request_key="withdraw"
    )
    state = db.rows[PaperResultPublicationState][item.paper_submission_id]
    assert state.active_publication_id is None
    assert await publish_key("K1") == a
    assert await publish_key("reuse") == reused
    assert (
        state.active_publication_id is None
        and len(db.rows[PaperResultPublication]) == 1
    )
    b = await publish_key("K2")
    assert b["id"] != a["id"] and state.active_publication_id == b["id"]
    assert await publish_key("K1") == a
    assert await publish_key("K2") == b
    assert (
        state.active_publication_id == b["id"]
        and len(db.rows[PaperResultPublication]) == 2
    )
    r2 = uuid4()
    newer = await publish_key("newer", r2)
    assert await publish_key("K1") == a
    assert await publish_key("K2") == b
    assert state.active_publication_id == newer["id"]
    assert len(db.rows[PaperResultPublication]) == 3
    assert len(db.rows[PaperResultPublicationWithdrawal]) == 1
    for paper_id, revision in [
        (item.paper_submission_id, r2),
        (uuid4(), item.approved_review_revision_id),
    ]:
        with pytest.raises(PaperPublicationError, match="request_key_conflict"):
            await repo.publish(paper_id, revision, actor, request_key="K1")


@pytest.mark.asyncio
async def test_bulk_command_lifecycle_preserves_all_historical_outcomes():
    db, actor, items = MemorySession(), uuid4(), entries()
    repo = memory_repository(db, items)
    original = await repo.publish_selected(items, actor, request_key="K1")
    assert (
        await repo.publish_selected(list(reversed(items)), actor, request_key="K1")
        == original
    )
    reused = await repo.publish_selected(items, actor, request_key="reuse")
    first = original[0]
    await repo.withdraw(
        first["paper_submission_id"],
        first["id"],
        "Correction",
        actor,
        request_key="withdraw",
    )
    second = original[1]
    newer = await repo.publish(
        second["paper_submission_id"], uuid4(), actor, request_key="newer"
    )
    states = db.rows[PaperResultPublicationState]
    before = {
        key: (row.active_publication_id, row.version) for key, row in states.items()
    }
    assert await repo.publish_selected(items, actor, request_key="K1") == original
    assert await repo.publish_selected(items, actor, request_key="reuse") == reused
    assert {
        key: (row.active_publication_id, row.version) for key, row in states.items()
    } == before
    assert states[second["paper_submission_id"]].active_publication_id == newer["id"]
    assert len(db.rows[PaperResultPublication]) == 4
    with pytest.raises(PaperPublicationError, match="request_key_conflict"):
        await repo.publish_selected(items[:2], actor, request_key="K1")
