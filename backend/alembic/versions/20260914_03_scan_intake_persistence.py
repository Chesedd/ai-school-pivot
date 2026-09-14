"""Add scanned-paper intake persistence foundation.

Revision ID: 20260914_03
Revises: 20260914_02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260914_03"
down_revision = "20260914_02"
branch_labels = None
depends_on = None
uuid = postgresql.UUID(as_uuid=True)
now = sa.text("clock_timestamp()")
BATCH = "'draft','uploading','extracting','matching','matching_review_required','grouping_confirmed','ready_for_checking','checking','checking_completed','completed','processing_failed','cancelled'"


def upgrade():
    op.create_table(
        "assessment_scan_batches",
        sa.Column(
            "id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "assignment_id",
            uuid,
            sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "class_group_id",
            uuid,
            sa.ForeignKey("class_groups.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            uuid,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("instruction_text", sa.Text, nullable=False),
        sa.Column("policy_draft", postgresql.JSONB),
        sa.Column("policy_snapshot", postgresql.JSONB),
        sa.Column("policy_schema_version", sa.String(64)),
        sa.Column("policy_fingerprint", sa.String(64)),
        sa.Column("prompt_policy_version", sa.String(64)),
        sa.Column("matching_revision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "cancelled_by_user_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT")
        ),
        sa.Column("cancellation_reason", sa.String(500)),
        sa.CheckConstraint(
            f"status IN ({BATCH})", name="ck_assessment_scan_batches_status"
        ),
        sa.CheckConstraint(
            "request_key=btrim(request_key) AND char_length(request_key) BETWEEN 1 AND 128",
            name="ck_assessment_scan_batches_request_key",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_assessment_scan_batches_request_hash",
        ),
        sa.CheckConstraint(
            "policy_fingerprint IS NULL OR policy_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_assessment_scan_batches_policy_fingerprint",
        ),
        sa.CheckConstraint(
            "matching_revision>=0 AND row_version>0",
            name="ck_assessment_scan_batches_versions",
        ),
        sa.CheckConstraint(
            "(status='cancelled' AND cancelled_at IS NOT NULL AND cancelled_by_user_id IS NOT NULL AND cancellation_reason IS NOT NULL) OR (status<>'cancelled' AND cancelled_at IS NULL AND cancelled_by_user_id IS NULL AND cancellation_reason IS NULL)",
            name="ck_assessment_scan_batches_cancellation",
        ),
        sa.CheckConstraint(
            "cancellation_reason IS NULL OR (cancellation_reason=btrim(cancellation_reason) AND char_length(cancellation_reason) BETWEEN 1 AND 500)",
            name="ck_assessment_scan_batches_cancel_reason",
        ),
        sa.UniqueConstraint(
            "created_by_user_id",
            "request_key",
            name="uq_assessment_scan_batches_actor_request",
        ),
    )
    op.create_index(
        "ix_assessment_scan_batches_assignment_created",
        "assessment_scan_batches",
        ["assignment_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_assessment_scan_batches_group_status",
        "assessment_scan_batches",
        ["class_group_id", "status"],
    )
    op.create_table(
        "scan_batch_artifacts",
        sa.Column(
            "id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "batch_id",
            uuid,
            sa.ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "input_artifact_id",
            uuid,
            sa.ForeignKey("input_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("upload_position", sa.Integer, nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column(
            "extraction_status", sa.String(32), nullable=False, server_default="pending"
        ),
        sa.Column("page_count", sa.Integer),
        sa.Column("failure_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.CheckConstraint(
            "upload_position>=0", name="ck_scan_batch_artifacts_position"
        ),
        sa.CheckConstraint(
            "original_filename=btrim(original_filename) AND char_length(original_filename) BETWEEN 1 AND 255",
            name="ck_scan_batch_artifacts_filename",
        ),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count>=0",
            name="ck_scan_batch_artifacts_page_count",
        ),
        sa.CheckConstraint(
            "extraction_status IN ('pending','running','completed','failed_retryable','failed_terminal')",
            name="ck_scan_batch_artifacts_status",
        ),
        sa.CheckConstraint(
            "extraction_status<>'completed' OR page_count IS NOT NULL",
            name="ck_scan_batch_artifacts_completed",
        ),
        sa.CheckConstraint(
            "(extraction_status IN ('failed_retryable','failed_terminal') AND failure_code IS NOT NULL) OR (extraction_status NOT IN ('failed_retryable','failed_terminal') AND failure_code IS NULL)",
            name="ck_scan_batch_artifacts_failure",
        ),
        sa.UniqueConstraint(
            "batch_id", "input_artifact_id", name="uq_scan_batch_artifacts_input"
        ),
        sa.UniqueConstraint(
            "batch_id", "upload_position", name="uq_scan_batch_artifacts_position"
        ),
    )
    op.create_index(
        "ix_scan_batch_artifacts_batch_status",
        "scan_batch_artifacts",
        ["batch_id", "extraction_status"],
    )
    op.create_table(
        "scan_pages",
        sa.Column(
            "id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "batch_artifact_id",
            uuid,
            sa.ForeignKey("scan_batch_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_page_index", sa.Integer, nullable=False),
        sa.Column(
            "derived_render_artifact_id",
            uuid,
            sa.ForeignKey("input_artifacts.id", ondelete="RESTRICT"),
        ),
        sa.Column("width_px", sa.Integer),
        sa.Column("height_px", sa.Integer),
        sa.Column(
            "rotation_degrees", sa.SmallInteger, nullable=False, server_default="0"
        ),
        sa.Column("coordinate_space_version", sa.String(64)),
        sa.Column("content_fingerprint", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("failure_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.CheckConstraint("source_page_index>=0", name="ck_scan_pages_index"),
        sa.CheckConstraint(
            "(width_px IS NULL OR width_px>0) AND (height_px IS NULL OR height_px>0)",
            name="ck_scan_pages_dimensions",
        ),
        sa.CheckConstraint(
            "rotation_degrees IN (0,90,180,270)", name="ck_scan_pages_rotation"
        ),
        sa.CheckConstraint(
            "status IN ('pending','ready','failed_retryable','failed_terminal')",
            name="ck_scan_pages_status",
        ),
        sa.CheckConstraint(
            "content_fingerprint IS NULL OR content_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_scan_pages_fingerprint",
        ),
        sa.CheckConstraint(
            "status<>'ready' OR (width_px>0 AND height_px>0 AND coordinate_space_version='normalized_upright_v1' AND content_fingerprint IS NOT NULL)",
            name="ck_scan_pages_ready",
        ),
        sa.CheckConstraint(
            "(status IN ('failed_retryable','failed_terminal') AND failure_code IS NOT NULL) OR (status NOT IN ('failed_retryable','failed_terminal') AND failure_code IS NULL)",
            name="ck_scan_pages_failure",
        ),
        sa.UniqueConstraint(
            "batch_artifact_id",
            "source_page_index",
            name="uq_scan_pages_artifact_index",
        ),
    )
    op.create_index(
        "ix_scan_pages_artifact_index",
        "scan_pages",
        ["batch_artifact_id", "source_page_index"],
    )
    op.create_table(
        "scan_checking_events",
        sa.Column(
            "id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "batch_id",
            uuid,
            sa.ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("aggregate_type", sa.String(16), nullable=False),
        sa.Column("aggregate_id", uuid, nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "actor_user_id",
            uuid,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "details",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now,
        ),
        sa.CheckConstraint(
            "aggregate_type IN ('batch','artifact','page')",
            name="ck_scan_checking_events_aggregate",
        ),
        sa.CheckConstraint(
            "event_type IN ('batch.created','batch.cancelled','artifact.attached','artifact.extraction_status_changed','page.created','page.status_changed')",
            name="ck_scan_checking_events_type",
        ),
    )
    op.create_index(
        "ix_scan_checking_events_batch_occurred",
        "scan_checking_events",
        ["batch_id", sa.text("occurred_at DESC"), sa.text("id DESC")],
    )


def downgrade():
    op.drop_table("scan_checking_events")
    op.drop_table("scan_pages")
    op.drop_table("scan_batch_artifacts")
    op.drop_table("assessment_scan_batches")
