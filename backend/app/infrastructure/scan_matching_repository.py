"""Short-transaction SQLAlchemy persistence for immutable matching evidence."""

from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import func, select
from app.infrastructure.scan_checking_models import (
    AssessmentScanBatch,
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
        )

    async def current_run(self, batch_id):
        return await self.session.scalar(
            select(ScanMatchingRun)
            .where(ScanMatchingRun.batch_id == batch_id)
            .order_by(ScanMatchingRun.revision.desc())
            .limit(1)
        )

    async def claim_chunk(self, run_id, index):
        row = await self.session.scalar(
            select(ScanMatchingChunk)
            .where(
                ScanMatchingChunk.matching_run_id == run_id,
                ScanMatchingChunk.chunk_index == index,
            )
            .with_for_update()
        )
        if row is None or row.status not in {"pending", "failed_retryable"}:
            return None
        row.status = "running"
        row.started_at = datetime.now(timezone.utc)
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
