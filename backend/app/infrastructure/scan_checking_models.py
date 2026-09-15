"""Persistence mappings for teacher-owned scanned-paper intake."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models import Base, IdMixin, uuid_type

clock = text("clock_timestamp()")
BATCH_STATES = "'draft','uploading','extracting','matching','matching_review_required','grouping_confirmed','ready_for_checking','checking','checking_completed','completed','processing_failed','cancelled'"


class AssessmentScanBatch(IdMixin, Base):
    __tablename__ = "assessment_scan_batches"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({BATCH_STATES})", name="ck_assessment_scan_batches_status"
        ),
        CheckConstraint(
            "request_key=btrim(request_key) AND char_length(request_key) BETWEEN 1 AND 128",
            name="ck_assessment_scan_batches_request_key",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_assessment_scan_batches_request_hash",
        ),
        CheckConstraint(
            "policy_fingerprint IS NULL OR policy_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_assessment_scan_batches_policy_fingerprint",
        ),
        CheckConstraint(
            "matching_revision >= 0 AND row_version > 0",
            name="ck_assessment_scan_batches_versions",
        ),
        CheckConstraint(
            "(status='cancelled' AND cancelled_at IS NOT NULL AND cancelled_by_user_id IS NOT NULL AND cancellation_reason IS NOT NULL) OR (status<>'cancelled' AND cancelled_at IS NULL AND cancelled_by_user_id IS NULL AND cancellation_reason IS NULL)",
            name="ck_assessment_scan_batches_cancellation",
        ),
        CheckConstraint(
            "cancellation_reason IS NULL OR (cancellation_reason=btrim(cancellation_reason) AND char_length(cancellation_reason) BETWEEN 1 AND 500)",
            name="ck_assessment_scan_batches_cancel_reason",
        ),
        UniqueConstraint(
            "created_by_user_id",
            "request_key",
            name="uq_assessment_scan_batches_actor_request",
        ),
        Index(
            "ix_assessment_scan_batches_assignment_created",
            "assignment_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index("ix_assessment_scan_batches_group_status", "class_group_id", "status"),
    )
    assignment_id: Mapped[UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT")
    )
    class_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_groups.id", ondelete="RESTRICT")
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(
        String(32), default="draft", server_default="draft"
    )
    instruction_text: Mapped[str] = mapped_column(Text)
    policy_draft: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    policy_snapshot: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    policy_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matching_revision: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    request_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ScanBatchArtifact(IdMixin, Base):
    __tablename__ = "scan_batch_artifacts"
    __table_args__ = (
        CheckConstraint(
            "upload_position >= 0", name="ck_scan_batch_artifacts_position"
        ),
        CheckConstraint(
            "original_filename=btrim(original_filename) AND char_length(original_filename) BETWEEN 1 AND 255",
            name="ck_scan_batch_artifacts_filename",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count >= 0",
            name="ck_scan_batch_artifacts_page_count",
        ),
        CheckConstraint(
            "extraction_status IN ('pending','running','completed','failed_retryable','failed_terminal')",
            name="ck_scan_batch_artifacts_status",
        ),
        CheckConstraint(
            "(extraction_status='completed' AND page_count IS NOT NULL) OR extraction_status<>'completed'",
            name="ck_scan_batch_artifacts_completed",
        ),
        CheckConstraint(
            "(extraction_status IN ('failed_retryable','failed_terminal') AND failure_code IS NOT NULL) OR (extraction_status NOT IN ('failed_retryable','failed_terminal') AND failure_code IS NULL)",
            name="ck_scan_batch_artifacts_failure",
        ),
        UniqueConstraint(
            "batch_id", "input_artifact_id", name="uq_scan_batch_artifacts_input"
        ),
        UniqueConstraint(
            "batch_id", "upload_position", name="uq_scan_batch_artifacts_position"
        ),
        Index("ix_scan_batch_artifacts_batch_status", "batch_id", "extraction_status"),
    )
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT")
    )
    input_artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey("input_artifacts.id", ondelete="RESTRICT")
    )
    upload_position: Mapped[int] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(255))
    extraction_status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending"
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )


class ScanPage(IdMixin, Base):
    """Dimensions describe the upright normalized render, never the source PDF."""

    __tablename__ = "scan_pages"
    __table_args__ = (
        CheckConstraint("source_page_index >= 0", name="ck_scan_pages_index"),
        CheckConstraint(
            "(width_px IS NULL OR width_px>0) AND (height_px IS NULL OR height_px>0)",
            name="ck_scan_pages_dimensions",
        ),
        CheckConstraint(
            "rotation_degrees IN (0,90,180,270)", name="ck_scan_pages_rotation"
        ),
        CheckConstraint(
            "status IN ('pending','ready','failed_retryable','failed_terminal')",
            name="ck_scan_pages_status",
        ),
        CheckConstraint(
            "content_fingerprint IS NULL OR content_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_scan_pages_fingerprint",
        ),
        CheckConstraint(
            "status<>'ready' OR (width_px>0 AND height_px>0 AND coordinate_space_version='normalized_upright_v1' AND content_fingerprint IS NOT NULL)",
            name="ck_scan_pages_ready",
        ),
        CheckConstraint(
            "(status IN ('failed_retryable','failed_terminal') AND failure_code IS NOT NULL) OR (status NOT IN ('failed_retryable','failed_terminal') AND failure_code IS NULL)",
            name="ck_scan_pages_failure",
        ),
        UniqueConstraint(
            "batch_artifact_id",
            "source_page_index",
            name="uq_scan_pages_artifact_index",
        ),
        Index("ix_scan_pages_artifact_index", "batch_artifact_id", "source_page_index"),
    )
    batch_artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_batch_artifacts.id", ondelete="RESTRICT")
    )
    source_page_index: Mapped[int] = mapped_column(Integer)
    derived_render_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("input_artifacts.id", ondelete="RESTRICT"), nullable=True
    )
    width_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rotation_degrees: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default="0"
    )
    coordinate_space_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending"
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )


class ScanCheckingEvent(IdMixin, Base):
    __tablename__ = "scan_checking_events"
    __table_args__ = (
        CheckConstraint(
            "aggregate_type IN ('batch','artifact','page','grouping','paper_submission')",
            name="ck_scan_checking_events_aggregate",
        ),
        CheckConstraint(
            "event_type IN ('batch.created','batch.cancelled','artifact.attached','artifact.extraction_status_changed','page.created','page.status_changed','grouping.draft_created','grouping.updated','grouping.confirmed','paper_submission.created')",
            name="ck_scan_checking_events_type",
        ),
        Index(
            "ix_scan_checking_events_batch_occurred",
            "batch_id",
            text("occurred_at DESC"),
            text("id DESC"),
        ),
    )
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT")
    )
    aggregate_type: Mapped[str] = mapped_column(String(16))
    aggregate_id: Mapped[UUID] = mapped_column(uuid_type)
    event_type: Mapped[str] = mapped_column(String(64))
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    details: Mapped[object] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )


class ScanMatchingRun(IdMixin, Base):
    __tablename__ = "scan_matching_runs"
    __table_args__ = (
        UniqueConstraint("batch_id", "revision", name="uq_scan_matching_runs_revision"),
        CheckConstraint("revision > 0", name="ck_scan_matching_runs_revision"),
        CheckConstraint(
            "status IN ('running','succeeded','failed_retryable','failed_terminal')",
            name="ck_scan_matching_runs_status",
        ),
        CheckConstraint(
            "request_context_fingerprint ~ '^[0-9a-f]{64}$' AND prompt_template_hash ~ '^[0-9a-f]{64}$'",
            name="ck_scan_matching_runs_hashes",
        ),
        CheckConstraint(
            "(status='running' AND completed_at IS NULL AND failure_code IS NULL) OR (status='succeeded' AND completed_at IS NOT NULL AND failure_code IS NULL) OR (status IN ('failed_retryable','failed_terminal') AND completed_at IS NOT NULL AND failure_code IS NOT NULL)",
            name="ck_scan_matching_runs_lifecycle",
        ),
    )
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT")
    )
    revision: Mapped[int] = mapped_column(Integer)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), server_default="running")
    prompt_name: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32))
    prompt_template_hash: Mapped[str] = mapped_column(String(64))
    output_schema_version: Mapped[str] = mapped_column(String(64))
    provider_route: Mapped[str] = mapped_column(String(256))
    request_context_fingerprint: Mapped[str] = mapped_column(String(64))
    assessment_title_snapshot: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))


class ScanMatchingRosterEntry(Base):
    __tablename__ = "scan_matching_roster_entries"
    __table_args__ = (
        UniqueConstraint(
            "matching_run_id",
            "assignment_participant_id",
            name="uq_scan_matching_roster_participant",
        ),
        UniqueConstraint(
            "matching_run_id", "position", name="uq_scan_matching_roster_position"
        ),
    )
    matching_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_matching_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    roster_token: Mapped[str] = mapped_column(String(128), primary_key=True)
    assignment_participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("assignment_participants.id", ondelete="RESTRICT")
    )
    display_name_snapshot: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer)


class ScanMatchingPageEntry(Base):
    __tablename__ = "scan_matching_page_entries"
    __table_args__ = (
        UniqueConstraint(
            "matching_run_id", "scan_page_id", name="uq_scan_matching_page"
        ),
        UniqueConstraint(
            "matching_run_id", "source_order", name="uq_scan_matching_source_order"
        ),
    )
    matching_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_matching_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    page_token: Mapped[str] = mapped_column(String(128), primary_key=True)
    scan_page_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_pages.id", ondelete="RESTRICT")
    )
    source_order: Mapped[int] = mapped_column(Integer)


class ScanMatchingChunk(IdMixin, Base):
    __tablename__ = "scan_matching_chunks"
    __table_args__ = (
        UniqueConstraint(
            "matching_run_id", "chunk_index", name="uq_scan_matching_chunks_index"
        ),
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed_retryable','failed_terminal')",
            name="ck_scan_matching_chunks_status",
        ),
    )
    matching_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_matching_runs.id", ondelete="RESTRICT")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), server_default="pending")
    primary_page_tokens: Mapped[object] = mapped_column(JSONB)
    context_page_tokens: Mapped[object] = mapped_column(JSONB)
    provider_id: Mapped[str] = mapped_column(String(128))
    model_id: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    provider_request_id: Mapped[str | None] = mapped_column(String(256))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    validated_output: Mapped[object | None] = mapped_column(JSONB)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScanPageMatchProposal(IdMixin, Base):
    __tablename__ = "scan_page_match_proposals"
    __table_args__ = (
        UniqueConstraint(
            "matching_run_id", "scan_page_id", name="uq_scan_page_match_proposal"
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_scan_page_match_confidence"
        ),
        CheckConstraint(
            "(disposition='matched' AND proposed_assignment_participant_id IS NOT NULL) OR (disposition IN ('ambiguous','unmatched') AND proposed_assignment_participant_id IS NULL)",
            name="ck_scan_page_match_disposition",
        ),
    )
    matching_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_matching_runs.id", ondelete="RESTRICT")
    )
    scan_page_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_pages.id", ondelete="RESTRICT")
    )
    disposition: Mapped[str] = mapped_column(String(16))
    proposed_assignment_participant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("assignment_participants.id", ondelete="RESTRICT")
    )
    confidence: Mapped[object] = mapped_column(Numeric(8, 7))
    evidence_code: Mapped[str] = mapped_column(String(128))
    evidence_summary: Mapped[str] = mapped_column(String(4000))
    proposed_group_token: Mapped[str | None] = mapped_column(String(128))
    proposed_page_order: Mapped[int | None] = mapped_column(Integer)
    provider_requires_human_review: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )


class ScanPageMatchCandidate(Base):
    __tablename__ = "scan_page_match_candidates"
    __table_args__ = (
        UniqueConstraint(
            "matching_run_id",
            "scan_page_id",
            "rank",
            name="uq_scan_page_match_candidate_rank",
        ),
        CheckConstraint(
            "rank BETWEEN 1 AND 8", name="ck_scan_page_match_candidate_rank"
        ),
    )
    matching_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_matching_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    scan_page_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_pages.id", ondelete="RESTRICT"), primary_key=True
    )
    assignment_participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("assignment_participants.id", ondelete="RESTRICT"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(Integer)


class ScanGroupingRevision(IdMixin, Base):
    __tablename__ = "scan_grouping_revisions"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "revision", name="uq_scan_grouping_revisions_revision"
        ),
        CheckConstraint(
            "revision > 0 AND based_on_matching_revision > 0 AND row_version > 0",
            name="ck_scan_grouping_revisions_versions",
        ),
        CheckConstraint(
            "status IN ('draft','confirmed','superseded')",
            name="ck_scan_grouping_revisions_status",
        ),
        CheckConstraint(
            "(status='confirmed' AND confirmed_at IS NOT NULL AND confirmed_by_user_id IS NOT NULL) OR (status<>'confirmed' AND confirmed_at IS NULL AND confirmed_by_user_id IS NULL)",
            name="ck_scan_grouping_revisions_confirmation",
        ),
        Index(
            "uq_scan_grouping_revisions_one_draft",
            "batch_id",
            unique=True,
            postgresql_where=text("status='draft'"),
        ),
    )
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT")
    )
    revision: Mapped[int] = mapped_column(Integer)
    based_on_matching_revision: Mapped[int] = mapped_column(Integer)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), server_default="draft")
    row_version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )


class ScanGroupingEntry(IdMixin, Base):
    __tablename__ = "scan_grouping_entries"
    __table_args__ = (
        UniqueConstraint(
            "id", "grouping_revision_id", name="uq_scan_grouping_entries_id_revision"
        ),
        UniqueConstraint(
            "grouping_revision_id",
            "assignment_participant_id",
            name="uq_scan_grouping_entries_participant",
        ),
        UniqueConstraint(
            "grouping_revision_id", "position", name="uq_scan_grouping_entries_position"
        ),
        CheckConstraint("position >= 0", name="ck_scan_grouping_entries_position"),
    )
    grouping_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_grouping_revisions.id", ondelete="RESTRICT")
    )
    assignment_participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("assignment_participants.id", ondelete="RESTRICT")
    )
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )


class ScanGroupingPage(Base):
    __tablename__ = "scan_grouping_pages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["grouping_entry_id", "grouping_revision_id"],
            ["scan_grouping_entries.id", "scan_grouping_entries.grouping_revision_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "grouping_revision_id",
            "scan_page_id",
            name="uq_scan_grouping_pages_revision_page",
        ),
        UniqueConstraint(
            "grouping_entry_id", "page_order", name="uq_scan_grouping_pages_order"
        ),
        CheckConstraint("page_order >= 0", name="ck_scan_grouping_pages_order"),
    )
    grouping_entry_id: Mapped[UUID] = mapped_column(uuid_type, primary_key=True)
    grouping_revision_id: Mapped[UUID] = mapped_column(uuid_type, primary_key=True)
    scan_page_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_pages.id", ondelete="RESTRICT"), primary_key=True
    )
    page_order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )


class ScanGroupingUnmatchedPage(Base):
    __tablename__ = "scan_grouping_unmatched_pages"
    grouping_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_grouping_revisions.id", ondelete="RESTRICT"), primary_key=True
    )
    scan_page_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_pages.id", ondelete="RESTRICT"), primary_key=True
    )
    reason_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )


class PaperSubmission(IdMixin, Base):
    __tablename__ = "paper_submissions"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "assignment_participant_id",
            name="uq_paper_submissions_batch_participant",
        ),
        UniqueConstraint(
            "grouping_revision_id",
            "assignment_participant_id",
            name="uq_paper_submissions_revision_participant",
        ),
        CheckConstraint(
            "status='ready_for_checking'", name="ck_paper_submissions_status"
        ),
    )
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT")
    )
    grouping_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_grouping_revisions.id", ondelete="RESTRICT")
    )
    assignment_id: Mapped[UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT")
    )
    assignment_participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("assignment_participants.id", ondelete="RESTRICT")
    )
    student_id: Mapped[UUID] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT")
    )
    assigned_variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_variants.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), server_default="ready_for_checking")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )


class PaperSubmissionPage(Base):
    __tablename__ = "paper_submission_pages"
    __table_args__ = (
        UniqueConstraint(
            "paper_submission_id", "page_order", name="uq_paper_submission_pages_order"
        ),
    )
    paper_submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("paper_submissions.id", ondelete="RESTRICT"), primary_key=True
    )
    scan_page_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_pages.id", ondelete="RESTRICT"), primary_key=True, unique=True
    )
    page_order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
