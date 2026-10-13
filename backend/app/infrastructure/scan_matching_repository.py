"""Short-transaction SQLAlchemy persistence for immutable matching evidence."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.infrastructure.scan_checking_models import (
    AssessmentScanBatch,
    ScanMatchingAttempt,
    ScanMatchingChunk,
    ScanMatchingRun,
    ScanPageMatchProposal,
)


class SqlAlchemyScanMatchingRepository:
    def __init__(self, session):
        self.session = session

    async def lock_batch(self, batch_id):
        return await self.session.scalar(
            select(AssessmentScanBatch)
            .where(AssessmentScanBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def current_run(self, batch_id):
        return await self.session.scalar(
            select(ScanMatchingRun)
            .where(ScanMatchingRun.batch_id == batch_id)
            .order_by(ScanMatchingRun.revision.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )

    async def claim_run(self, run):
        # Serialize with an old finalizer renewing/persisting its chunk, as well
        # as other recovery claims (caller already holds the batch lock).
        run = await self.session.scalar(select(ScanMatchingRun).where(
            ScanMatchingRun.id == run.id).with_for_update().execution_options(populate_existing=True))
        if run.status not in {"running", "failed_retryable"}:
            return None
        now = await self.session.scalar(select(func.clock_timestamp()))
        if run.status == "running" and run.claim_token is not None and run.lease_expires_at > now:
            return None
        # Successful chunks and their evidence are never reset on recovery.
        chunks = (await self.session.scalars(select(ScanMatchingChunk).where(
            ScanMatchingChunk.matching_run_id == run.id))).all()
        for chunk in chunks:
            if chunk.status == "running":
                chunk.status = "pending"
                attempts = (await self.session.scalars(select(ScanMatchingAttempt).where(
                    ScanMatchingAttempt.chunk_id == chunk.id,
                    ScanMatchingAttempt.status == "running"))).all()
                for attempt in attempts:
                    attempt.status = "interrupted"
                    attempt.completed_at = now
                    attempt.failure_code = "matching_lease_expired"
        run.claim_token = uuid4()
        run.lease_expires_at = now + timedelta(minutes=15)
        run.status = "running"
        run.completed_at = None
        run.failure_code = None
        return run.claim_token

    async def fenced_run(self, run_id, token):
        run = await self.session.scalar(select(ScanMatchingRun).where(
            ScanMatchingRun.id == run_id).with_for_update().execution_options(populate_existing=True))
        now = await self.session.scalar(select(func.clock_timestamp()))
        if run is None or run.status != "running" or run.claim_token != token or run.lease_expires_at <= now:
            return None
        run.lease_expires_at = now + timedelta(minutes=15)
        return run

    async def claim_chunk(self, run_id, index, token):
        if await self.fenced_run(run_id, token) is None:
            return None
        row = await self.session.scalar(
            select(ScanMatchingChunk)
            .where(
                ScanMatchingChunk.matching_run_id == run_id,
                ScanMatchingChunk.chunk_index == index,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.status not in {"pending", "failed_retryable"}:
            return None
        row.status = "running"
        row.started_at = await self.session.scalar(select(func.clock_timestamp()))
        row.completed_at = None
        row.failure_code = None
        await self.session.commit()
        return row

    async def proposal_count(self, run_id):
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(ScanPageMatchProposal)
                .where(ScanPageMatchProposal.matching_run_id == run_id)
            )
            or 0
        )
