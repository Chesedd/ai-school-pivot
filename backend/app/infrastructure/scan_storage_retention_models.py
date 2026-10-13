"""Operational purge ledger; academic rows remain intact."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models import Base, IdMixin, uuid_type


class ScanStorageRetentionEntry(IdMixin, Base):
    __tablename__ = "scan_storage_retention_entries"
    # PR14 requires its claim writer to supply the ledger UUID explicitly.
    # Unlike other IdMixin tables, the accepted schema has no ID server default.
    id: Mapped[UUID] = mapped_column(uuid_type, primary_key=True)
    __table_args__ = (
        CheckConstraint(
            "(target_kind='source_artifact' AND batch_artifact_id IS NOT NULL AND scan_page_id IS NULL) OR (target_kind='page_render' AND batch_artifact_id IS NULL AND scan_page_id IS NOT NULL)",
            name="ck_scan_retention_target",
        ),
        CheckConstraint(
            "state IN ('pending','purged','retryable_failed')",
            name="ck_scan_retention_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_scan_retention_attempts"),
        CheckConstraint(
            "(claimed_at IS NULL AND claim_expires_at IS NULL AND claim_token IS NULL) OR (claimed_at IS NOT NULL AND claim_expires_at > claimed_at AND claim_token IS NOT NULL)",
            name="ck_scan_retention_claim",
        ),
        CheckConstraint(
            "(state='purged') = (purged_at IS NOT NULL)",
            name="ck_scan_retention_purged",
        ),
        UniqueConstraint("batch_artifact_id", name="uq_scan_retention_artifact"),
        UniqueConstraint("scan_page_id", name="uq_scan_retention_page"),
        Index("ix_scan_retention_claim", "state", "claim_expires_at", "eligible_at"),
    )
    scan_batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT")
    )
    target_kind: Mapped[str] = mapped_column(String(32))
    batch_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scan_batch_artifacts.id", ondelete="RESTRICT")
    )
    scan_page_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scan_pages.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(32), server_default="pending")
    policy_version: Mapped[str] = mapped_column(String(64))
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[UUID | None] = mapped_column(uuid_type)
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
