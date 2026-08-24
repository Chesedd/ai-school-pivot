"""Persistence mappings dedicated to image solving (not authoring)."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
