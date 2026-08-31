"""SQLAlchemy repository for the image-solving aggregate."""
import json
from typing import TypeVar
from uuid import UUID
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.application.image_solving_contracts import ExtractionResultV1, ImageSolvingSession, ImageSolvingStatus, SolutionResultV1, ValidationResultV1
from app.infrastructure.image_solving_models import ImageSolvingCheckpointRow, ImageSolvingSessionRow, ImageSolvingMetadataRecommendationRow
from app.application.image_solving_metadata import ImageTaskMetadataRecommendationV1
from app.application.image_solving_api import ImageSolvingAttempt
CONTRACTS = {"extraction": ExtractionResultV1, "solver": SolutionResultV1, "validation": ValidationResultV1}
Contract = TypeVar("Contract", bound=BaseModel)


def deserialize_json_contract(payload: object, contract: type[Contract]) -> Contract:
    """Restore a strict contract through the same JSON boundary used by JSONB."""
    try:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return contract.model_validate_json(serialized)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("invalid_checkpoint") from exc


# Compatibility for callers predating the shared public JSON-boundary helper.
_deserialize_checkpoint = deserialize_json_contract

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
            value = deserialize_json_contract(item.payload, contract)
            if value.fingerprint != item.fingerprint: raise ValueError("invalid_checkpoint")
            checkpoints[item.stage] = value
        return ImageSolvingSession(session_id=row.id, owner_id=row.owner_id, input_artifact_id=row.input_artifact_id, extraction_checkpoint=checkpoints.get("extraction"), solver_checkpoint=checkpoints.get("solver"), validation_checkpoint=checkpoints.get("validation"), lifecycle_status=ImageSolvingStatus(row.status), failure_code=row.failure_code, created_at=row.created_at, updated_at=row.updated_at)
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
    async def retry_solver(self, session_id: UUID) -> bool:
        """Explicitly reopen only a failed session whose extraction is durable."""
        result = await self.db.execute(update(ImageSolvingSessionRow).where(
            ImageSolvingSessionRow.id == session_id,
            ImageSolvingSessionRow.status == "failed",
            ImageSolvingSessionRow.failure_code.in_(("timeout", "connection_error",
                "provider_unavailable", "malformed_response", "output_budget")),
        ).values(status="extracted", failure_code=None,
            updated_at=func.clock_timestamp()))
        await self.db.commit()
        return result.rowcount == 1
    async def attempts(self, session_id: UUID) -> tuple[ImageSolvingAttempt, ...]:
        rows = (await self.db.execute(select(ImageSolvingCheckpointRow).where(
            ImageSolvingCheckpointRow.session_id == session_id).order_by(
            ImageSolvingCheckpointRow.created_at, ImageSolvingCheckpointRow.id))).scalars().all()
        return tuple(ImageSolvingAttempt(stage=row.stage, provider_id=row.provider_id,
            model_id=row.model_id, input_tokens=row.input_tokens, output_tokens=row.output_tokens,
            cost_amount=row.cost_amount, currency=row.currency,
            provider_request_id=row.provider_request_id, created_at=row.created_at) for row in rows)

    async def get_recommendation(self, session_id: UUID):
        row=await self.db.scalar(select(ImageSolvingMetadataRecommendationRow).where(
            ImageSolvingMetadataRecommendationRow.session_id==session_id))
        return None if row is None else deserialize_json_contract(row.payload,ImageTaskMetadataRecommendationV1)

    async def save_recommendation(self, session_id, value, catalog_fingerprint):
        self.db.add(ImageSolvingMetadataRecommendationRow(session_id=session_id,
            payload=value.model_dump(mode="json"),catalog_fingerprint=catalog_fingerprint))
        try: await self.db.commit()
        except Exception:
            await self.db.rollback()
            cached=await self.get_recommendation(session_id)
            if cached is not None:return cached
            raise
        return value
