"""Reconcile only journal-proven newly allocated scanned-paper objects."""
from sqlalchemy import select

from app.infrastructure.authoring_models import InputArtifact
from app.infrastructure.scan_checking_models import AssessmentScanBatch


async def reconcile_scan_object(session, storage, batch_id, reference, *, dry_run=False):
    # A live writer holds this lock from before journaling through academic commit.
    # Acquiring it waits for commit/rollback, including a lost commit acknowledgement.
    batch = await session.scalar(select(AssessmentScanBatch).where(
        AssessmentScanBatch.id == batch_id).with_for_update())
    marker = storage.scan_marker(batch_id, reference)
    if batch is None or not marker.exists():
        return "unknown" if batch is None else "settled"
    registered = await session.scalar(select(InputArtifact.id).where(
        InputArtifact.storage_reference == reference).limit(1))
    if registered is not None:
        if not dry_run:
            storage.acknowledge_scan(batch_id, reference)
        return "committed"
    if dry_run:
        return "orphan"
    try:
        # False/already absent is successful, and replay after deletion is safe.
        await storage.delete(reference)
        storage.acknowledge_scan(batch_id, reference)
    except Exception:  # noqa: BLE001 - backend errors retain the durable marker.
        return "retryable_failed"
    return "purged"


async def compensate_scan_objects(session, storage, batch_id, references):
    for reference in references:
        try:
            await reconcile_scan_object(session, storage, batch_id, reference)
            await session.commit()
        except Exception:  # noqa: BLE001 - DB outage also leaves the journal recoverable.
            await session.rollback()


async def sweep_scan_orphans(factory, storage, *, dry_run=True):
    outcomes = {}
    for batch_id, reference in storage.pending_scan_objects():
        async with factory() as session:
            try:
                result = await reconcile_scan_object(session, storage, batch_id, reference, dry_run=dry_run)
                await session.commit()
            except Exception:  # noqa: BLE001 - retry on next explicit operator run.
                await session.rollback()
                result = "retryable_failed"
            outcomes[result] = outcomes.get(result, 0) + 1
    return outcomes
