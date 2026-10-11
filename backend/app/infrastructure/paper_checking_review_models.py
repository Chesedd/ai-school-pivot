"""Mutable teacher workspace and append-only review revision persistence."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models import Base, IdMixin, uuid_type

clock = text("clock_timestamp()")


class PaperCheckReview(IdMixin, Base):
    __tablename__ = "paper_check_reviews"
    __table_args__ = (
        CheckConstraint(
            "(source_kind='ai' AND check_run_id IS NOT NULL AND ai_revision_id IS NOT NULL) OR (source_kind='manual' AND check_run_id IS NULL AND ai_revision_id IS NULL)",
            name="ck_paper_check_reviews_source",
        ),
        UniqueConstraint("check_run_id", name="uq_paper_check_reviews_run"),
        Index(
            "uq_paper_check_reviews_manual",
            "paper_submission_id",
            unique=True,
            postgresql_where=text("source_kind = 'manual'"),
        ),
        UniqueConstraint(
            "id",
            "paper_submission_id",
            "source_kind",
            name="uq_paper_check_reviews_source",
        ),
        UniqueConstraint(
            "id",
            "paper_submission_id",
            "check_run_id",
            "ai_revision_id",
            name="uq_paper_check_reviews_provenance",
        ),
        CheckConstraint(
            "version > 0 AND state IN ('reviewing','approved')",
            name="ck_paper_check_reviews_state",
        ),
        CheckConstraint(
            "state <> 'approved' OR latest_approved_revision_id IS NOT NULL",
            name="ck_paper_check_reviews_approved",
        ),
        ForeignKeyConstraint(
            ["ai_revision_id", "paper_submission_id", "check_run_id"],
            [
                "paper_ai_revisions.id",
                "paper_ai_revisions.paper_submission_id",
                "paper_ai_revisions.check_run_id",
            ],
            name="fk_paper_check_reviews_ai_provenance",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["id", "latest_approved_revision_id"],
            [
                "paper_check_review_revisions.review_id",
                "paper_check_review_revisions.id",
            ],
            name="fk_paper_check_reviews_approved",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        Index("ix_paper_check_reviews_paper", "paper_submission_id"),
    )
    paper_submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("paper_submissions.id", ondelete="RESTRICT")
    )
    source_kind: Mapped[str] = mapped_column(String(16), server_default="ai")
    check_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("check_runs.id", ondelete="RESTRICT")
    )
    ai_revision_id: Mapped[UUID | None] = mapped_column(uuid_type)
    state: Mapped[str] = mapped_column(String(16), server_default="reviewing")
    draft: Mapped[object] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    latest_approved_revision_id: Mapped[UUID | None] = mapped_column(uuid_type)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    updated_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )


class PaperCheckReviewRevision(IdMixin, Base):
    __tablename__ = "paper_check_review_revisions"
    __table_args__ = (
        CheckConstraint(
            "(source_kind='ai' AND check_run_id IS NOT NULL AND ai_revision_id IS NOT NULL) OR (source_kind='manual' AND check_run_id IS NULL AND ai_revision_id IS NULL)",
            name="ck_paper_check_review_revisions_source",
        ),
        ForeignKeyConstraint(
            ["review_id", "paper_submission_id", "source_kind"],
            [
                "paper_check_reviews.id",
                "paper_check_reviews.paper_submission_id",
                "paper_check_reviews.source_kind",
            ],
            name="fk_paper_check_review_revisions_source",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "review_id",
            "revision_number",
            name="uq_paper_check_review_revisions_number",
        ),
        UniqueConstraint(
            "review_id", "id", name="uq_paper_check_review_revisions_identity"
        ),
        ForeignKeyConstraint(
            ["review_id", "paper_submission_id", "check_run_id", "ai_revision_id"],
            [
                "paper_check_reviews.id",
                "paper_check_reviews.paper_submission_id",
                "paper_check_reviews.check_run_id",
                "paper_check_reviews.ai_revision_id",
            ],
            name="fk_paper_check_review_revisions_provenance",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "revision_number >= 0 AND revision_kind IN ('ai_seed','manual_seed','human_edit','approved') AND ((revision_number=0) = (revision_kind IN ('ai_seed','manual_seed'))) AND (revision_kind <> 'ai_seed' OR source_kind='ai') AND (revision_kind <> 'manual_seed' OR source_kind='manual')",
            name="ck_paper_check_review_revisions_kind",
        ),
        CheckConstraint(
            "grading_policy_revision > 0 AND grading_policy_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_paper_check_review_revisions_policy",
        ),
        CheckConstraint(
            "(revision_kind='approved' AND approved_at IS NOT NULL AND calculated_total_score IS NOT NULL AND total_max_score IS NOT NULL AND calculated_percentage IS NOT NULL AND calculated_grade IS NOT NULL AND final_grade IS NOT NULL) OR (revision_kind<>'approved' AND approved_at IS NULL AND calculated_total_score IS NULL AND total_max_score IS NULL AND calculated_percentage IS NULL AND calculated_grade IS NULL AND final_grade IS NULL AND grade_override_reason IS NULL)",
            name="ck_paper_check_review_revisions_grade",
        ),
        CheckConstraint(
            "calculated_total_score >= 0 AND total_max_score > 0 AND calculated_total_score <= total_max_score AND calculated_percentage BETWEEN 0 AND 100",
            name="ck_paper_check_review_revisions_scores",
        ),
        CheckConstraint(
            "(grade_override_reason IS NULL AND final_grade=calculated_grade) OR (grade_override_reason IS NOT NULL AND grade_override_reason=btrim(grade_override_reason) AND char_length(grade_override_reason) BETWEEN 1 AND 2000)",
            name="ck_paper_check_review_revisions_override",
        ),
        CheckConstraint(
            "calculated_grade=btrim(calculated_grade) AND char_length(calculated_grade) BETWEEN 1 AND 64 AND final_grade=btrim(final_grade) AND char_length(final_grade) BETWEEN 1 AND 64",
            name="ck_paper_check_review_revisions_labels",
        ),
        Index(
            "ix_paper_check_review_revisions_history", "review_id", "revision_number"
        ),
    )
    review_id: Mapped[UUID] = mapped_column(uuid_type)
    paper_submission_id: Mapped[UUID] = mapped_column(uuid_type)
    source_kind: Mapped[str] = mapped_column(String(16), server_default="ai")
    check_run_id: Mapped[UUID | None] = mapped_column(uuid_type)
    ai_revision_id: Mapped[UUID | None] = mapped_column(uuid_type)
    revision_number: Mapped[int] = mapped_column(Integer)
    revision_kind: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[object] = mapped_column(JSONB)
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    grading_policy_revision: Mapped[int] = mapped_column(Integer)
    grading_policy_fingerprint: Mapped[str] = mapped_column(String(64))
    change_summary: Mapped[object] = mapped_column(JSONB)
    calculated_total_score: Mapped[Decimal | None] = mapped_column(Numeric())
    total_max_score: Mapped[Decimal | None] = mapped_column(Numeric())
    calculated_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    calculated_grade: Mapped[str | None] = mapped_column(String(64))
    final_grade: Mapped[str | None] = mapped_column(String(64))
    grade_override_reason: Mapped[str | None] = mapped_column(String(2000))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
