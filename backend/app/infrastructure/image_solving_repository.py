"""SQLAlchemy repository for the image-solving aggregate."""
from uuid import UUID
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.application.image_solving_contracts import ExtractionResultV1, ImageSolvingSession, ImageSolvingStatus, SolutionResultV1, ValidationResultV1
from app.infrastructure.image_solving_models import ImageSolvingCheckpointRow, ImageSolvingSessionRow
CONTRACTS = {"extraction": ExtractionResultV1, "solver": SolutionResultV1, "validation": ValidationResultV1}

class SqlAlchemyImageSolvingRepository:
    """The only adapter aware of persisted checkpoint representation."""
    def __init__(self, db: AsyncSession): self.db = db
    async def create(self, owner_id: UUID, artifact_id: UUID) -> ImageSolvingSession:
        row = ImageSolvingSessionRow(owner_id=owner_id, input_artifact_id=artifact_id)
        self.db.add(row); await self.db.commit(); return await self.get(row.id)
    async def get(self, session_id: UUID) -> ImageSolvingSession | None:
        row = await self.db.get(ImageSolvingSessionRow, session_id)
        if row is None: return None
        values = (await self.db.execute(select(ImageSolvingCheckpointRow).where(ImageSolvingCheckpointRow.session_id == session_id))).scalars().all()
        checkpoints = {}
        for item in values:
            contract = CONTRACTS.get(item.stage)
            if contract is None: continue
            value = contract.model_validate(item.payload)
            if value.fingerprint != item.fingerprint: raise ValueError("invalid_checkpoint")
            checkpoints[item.stage] = value
        return ImageSolvingSession(session_id=row.id, owner_id=row.owner_id, input_artifact_id=row.input_artifact_id, extraction_checkpoint=checkpoints.get("extraction"), solver_checkpoint=checkpoints.get("solver"), validation_checkpoint=checkpoints.get("validation"), lifecycle_status=ImageSolvingStatus(row.status), created_at=row.created_at, updated_at=row.updated_at)
    async def claim(self, session_id: UUID, expected: ImageSolvingStatus, running: ImageSolvingStatus) -> bool:
        # A fresh running lease rejects concurrent work; an abandoned lease is
        # recoverable after five minutes without weakening the checkpoint CAS.
        claimable = or_(ImageSolvingSessionRow.status == expected.value,
            (ImageSolvingSessionRow.status == running.value) &
            (ImageSolvingSessionRow.updated_at < func.clock_timestamp() - text("interval '5 minutes'")))
        result = await self.db.execute(update(ImageSolvingSessionRow).where(
            ImageSolvingSessionRow.id == session_id, claimable
        ).values(status=running.value, updated_at=func.clock_timestamp()))
        await self.db.commit(); return result.rowcount == 1
    async def save_checkpoint(self, session_id: UUID, stage: str, payload, status: ImageSolvingStatus,
                              *, route=None, telemetry=None):
        """Persist only semantic output and normalized telemetry, never provider/raw data."""
        values = {}
        if route is not None and telemetry is not None:
            values = {"provider_id": route.provider_id, "model_id": route.model_id,
                "provider_request_id": telemetry.provider_request_id,
                "input_tokens": telemetry.usage.input_tokens,
                "output_tokens": telemetry.usage.output_tokens,
                "cost_amount": None if telemetry.cost is None else telemetry.cost.amount,
                "currency": None if telemetry.cost is None else telemetry.cost.currency}
        self.db.add(ImageSolvingCheckpointRow(session_id=session_id, stage=stage,
            payload=payload.model_dump(mode="json"), fingerprint=payload.fingerprint, **values))
        await self.db.execute(update(ImageSolvingSessionRow).where(ImageSolvingSessionRow.id == session_id).values(status=status.value, updated_at=func.clock_timestamp()))
        await self.db.commit(); return await self.get(session_id)
    async def fail(self, session_id: UUID, code: str) -> None:
        await self.db.execute(update(ImageSolvingSessionRow).where(ImageSolvingSessionRow.id == session_id).values(status="failed", failure_code=code, updated_at=func.clock_timestamp()))
        await self.db.commit()
