"""Append-only publication history and one mutable pointer per paper."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models import Base, IdMixin, uuid_type


class PaperResultPublication(IdMixin, Base):
    __tablename__ = "paper_result_publications"
    __table_args__ = (
        Index("ix_paper_publication_revision", "approved_review_revision_id"),
        UniqueConstraint(
            "paper_submission_id",
            "publication_number",
            name="uq_paper_publication_number",
        ),
        UniqueConstraint(
            "paper_submission_id", "id", name="uq_paper_publication_identity"
        ),
        ForeignKeyConstraint(
            ["review_id", "approved_review_revision_id"],
            [
                "paper_check_review_revisions.review_id",
                "paper_check_review_revisions.id",
            ],
            name="fk_paper_publication_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["paper_submission_id", "supersedes_publication_id"],
            [
                "paper_result_publications.paper_submission_id",
                "paper_result_publications.id",
            ],
            name="fk_paper_publication_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "publication_number > 0 AND (supersedes_publication_id IS NULL OR supersedes_publication_id <> id)",
            name="ck_paper_publication_number",
        ),
        CheckConstraint(
            "jsonb_typeof(publication_snapshot)='object' AND publication_snapshot->>'schema_version' IS NOT NULL AND publication_snapshot->>'schema_version'='paper_result_publication.v1'",
            name="ck_paper_publication_snapshot",
        ),
    )
    paper_submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("paper_submissions.id", ondelete="RESTRICT")
    )
    review_id: Mapped[UUID] = mapped_column(uuid_type)
    approved_review_revision_id: Mapped[UUID] = mapped_column(uuid_type)
    published_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    publication_number: Mapped[int] = mapped_column(Integer)
    supersedes_publication_id: Mapped[UUID | None] = mapped_column(uuid_type)
    publication_snapshot: Mapped[object] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class PaperResultPublicationState(Base):
    __tablename__ = "paper_result_publication_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["paper_submission_id", "active_publication_id"],
            [
                "paper_result_publications.paper_submission_id",
                "paper_result_publications.id",
            ],
            name="fk_paper_publication_active",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version > 0", name="ck_paper_publication_state_version"),
    )
    paper_submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("paper_submissions.id", ondelete="RESTRICT"), primary_key=True
    )
    active_publication_id: Mapped[UUID | None] = mapped_column(uuid_type)
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class PaperResultPublicationWithdrawal(IdMixin, Base):
    __tablename__ = "paper_result_publication_withdrawals"
    __table_args__ = (
        UniqueConstraint("publication_id", name="uq_paper_publication_withdrawal"),
        CheckConstraint(
            "length(btrim(reason, ' ' || chr(9) || chr(10) || chr(11) || chr(12) || chr(13))) > 0 AND length(reason) <= 500 AND reason = btrim(reason, ' ' || chr(9) || chr(10) || chr(11) || chr(12) || chr(13))",
            name="ck_paper_withdrawal_reason",
        ),
        Index("ix_paper_withdrawal_created", "created_at", "publication_id"),
    )
    publication_id: Mapped[UUID] = mapped_column(
        ForeignKey("paper_result_publications.id", ondelete="RESTRICT")
    )
    withdrawn_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class PaperResultPublicationCommand(IdMixin, Base):
    __tablename__ = "paper_result_publication_commands"
    __table_args__ = (
        UniqueConstraint(
            "actor_user_id", "request_key", name="uq_paper_publication_command_key"
        ),
        CheckConstraint(
            "length(request_key) BETWEEN 1 AND 128", name="ck_paper_command_key"
        ),
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    request_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    resolved_fingerprint: Mapped[str | None] = mapped_column(String(64))
    command_kind: Mapped[str] = mapped_column(String(32))
    item_count: Mapped[int] = mapped_column(Integer, server_default=text("-1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class PaperResultPublicationCommandItem(Base):
    __tablename__ = "paper_result_publication_command_items"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_paper_command_position"),
        CheckConstraint(
            "(publication_id IS NULL) <> (withdrawal_id IS NULL)",
            name="ck_paper_command_result",
        ),
    )
    command_id: Mapped[UUID] = mapped_column(
        ForeignKey("paper_result_publication_commands.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    publication_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("paper_result_publications.id", ondelete="RESTRICT")
    )
    withdrawal_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("paper_result_publication_withdrawals.id", ondelete="RESTRICT")
    )
    outcome: Mapped[str | None] = mapped_column(String(32))
