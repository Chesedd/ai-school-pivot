"""Authoritative aggregate reads. Caller holds the batch row lock until commit."""
from sqlalchemy import func, select

from app.application.scan_intake import ScanIntakeError
from app.infrastructure.authoring_models import InputArtifact
from app.infrastructure.scan_checking_models import ScanBatchArtifact, ScanPage


async def guard_attachment(session, batch_id, size, limits):
    count, total = (await session.execute(select(func.count(ScanBatchArtifact.id),
        func.coalesce(func.sum(InputArtifact.size_bytes), 0)).join(InputArtifact,
        InputArtifact.id == ScanBatchArtifact.input_artifact_id).where(
            ScanBatchArtifact.batch_id == batch_id))).one()
    limits.guard("max_single_artifact_bytes", size, "scan_artifact_too_large", ScanIntakeError)
    limits.guard("max_artifacts_per_batch", count + 1, "scan_batch_artifact_limit_exceeded", ScanIntakeError)
    limits.guard("max_total_upload_bytes_per_batch", total + size, "scan_batch_byte_limit_exceeded", ScanIntakeError)


async def page_count(session, batch_id):
    return await session.scalar(select(func.count(ScanPage.id)).join(ScanBatchArtifact,
        ScanBatchArtifact.id == ScanPage.batch_artifact_id).where(ScanBatchArtifact.batch_id == batch_id))
