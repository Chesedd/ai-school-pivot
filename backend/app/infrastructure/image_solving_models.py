"""Persistence mappings dedicated to image solving (not authoring)."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.models import Base, IdMixin, uuid_type

class ImageSolvingSessionRow(IdMixin, Base):
    __tablename__ = "image_solving_sessions"
    __table_args__ = (CheckConstraint("status IN ('created','extracting','extracted','solving','solved','validated','failed')", name="ck_image_solving_sessions_status"), Index("ix_image_solving_sessions_owner_created", "owner_id", "created_at"))
    owner_id: Mapped[UUID] = mapped_column(uuid_type)
    input_artifact_id: Mapped[UUID] = mapped_column(ForeignKey("input_artifacts.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(16), server_default="created")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))

class ImageSolvingCheckpointRow(IdMixin, Base):
    __tablename__ = "image_solving_checkpoints"
    __table_args__ = (CheckConstraint("stage IN ('extraction','solver','validation')", name="ck_image_solving_checkpoints_stage"), UniqueConstraint("session_id", "stage", name="uq_image_solving_checkpoints_stage"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("image_solving_sessions.id", ondelete="RESTRICT"))
    stage: Mapped[str] = mapped_column(String(16))
    payload: Mapped[object] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[str | None] = mapped_column(String(128))
    model_id: Mapped[str | None] = mapped_column(String(256))
    provider_request_id: Mapped[str | None] = mapped_column(String(256))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_amount: Mapped[object | None] = mapped_column(Numeric(20, 8))
    currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))

class ImageSolvingMetadataRecommendationRow(IdMixin, Base):
    __tablename__ = "image_solving_metadata_recommendations"
    __table_args__ = (UniqueConstraint("session_id", name="uq_image_solving_metadata_session"),)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("image_solving_sessions.id", ondelete="RESTRICT"))
    payload: Mapped[object] = mapped_column(JSONB)
    catalog_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
