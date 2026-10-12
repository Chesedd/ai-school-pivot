"""Scanned-paper purge ledger. Downgrade cannot restore purged storage bytes.

Revision ID: 20261012_01
Revises: 20261011_03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261012_01"
down_revision = "20261011_03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scan_storage_retention_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "scan_batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column(
            "batch_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scan_batch_artifacts.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "scan_page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scan_pages.id", ondelete="RESTRICT"),
        ),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "(target_kind='source_artifact' AND batch_artifact_id IS NOT NULL AND scan_page_id IS NULL) OR (target_kind='page_render' AND batch_artifact_id IS NULL AND scan_page_id IS NOT NULL)",
            name="ck_scan_retention_target",
        ),
        sa.CheckConstraint(
            "state IN ('pending','purged','retryable_failed')",
            name="ck_scan_retention_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scan_retention_attempts"),
        sa.CheckConstraint(
            "(claimed_at IS NULL AND claim_expires_at IS NULL AND claim_token IS NULL) OR (claimed_at IS NOT NULL AND claim_expires_at > claimed_at AND claim_token IS NOT NULL)",
            name="ck_scan_retention_claim",
        ),
        sa.CheckConstraint(
            "(state='purged') = (purged_at IS NOT NULL)",
            name="ck_scan_retention_purged",
        ),
        sa.UniqueConstraint("batch_artifact_id", name="uq_scan_retention_artifact"),
        sa.UniqueConstraint("scan_page_id", name="uq_scan_retention_page"),
    )
    op.create_index(
        "ix_scan_retention_claim",
        "scan_storage_retention_entries",
        ["state", "claim_expires_at", "eligible_at"],
    )
    op.create_index(
        "ix_scan_retention_completion",
        "scan_checking_events",
        ["batch_id", "occurred_at"],
        postgresql_where=sa.text("event_type='batch.completed'"),
    )
    op.execute("""
        CREATE FUNCTION guard_scan_completion_anchor() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.event_type='batch.completed' THEN
            RAISE EXCEPTION 'batch completion event is immutable' USING ERRCODE='23514';
          END IF;
          IF TG_OP='UPDATE' AND NEW.event_type='batch.completed' THEN
            RAISE EXCEPTION 'completion event must be inserted' USING ERRCODE='23514';
          END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER scan_completion_anchor_guard BEFORE UPDATE OR DELETE ON scan_checking_events FOR EACH ROW EXECUTE FUNCTION guard_scan_completion_anchor()"
    )
    op.execute("""
        CREATE FUNCTION guard_scan_retention_target() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.target_kind='source_artifact' AND NOT EXISTS (
            SELECT 1 FROM scan_batch_artifacts a WHERE a.id=NEW.batch_artifact_id AND a.batch_id=NEW.scan_batch_id))
          OR (NEW.target_kind='page_render' AND NOT EXISTS (
            SELECT 1 FROM scan_pages p JOIN scan_batch_artifacts a ON a.id=p.batch_artifact_id
            WHERE p.id=NEW.scan_page_id AND a.batch_id=NEW.scan_batch_id)) THEN
            RAISE EXCEPTION 'retention target batch mismatch' USING ERRCODE='23514';
          END IF;
          IF TG_OP='UPDATE' AND (OLD.scan_batch_id, OLD.target_kind, OLD.batch_artifact_id,
              OLD.scan_page_id, OLD.policy_version, OLD.eligible_at) IS DISTINCT FROM
              (NEW.scan_batch_id, NEW.target_kind, NEW.batch_artifact_id,
              NEW.scan_page_id, NEW.policy_version, NEW.eligible_at) THEN
            RAISE EXCEPTION 'immutable retention identity' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER scan_retention_target_guard BEFORE INSERT OR UPDATE ON scan_storage_retention_entries FOR EACH ROW EXECUTE FUNCTION guard_scan_retention_target()"
    )


def downgrade():
    # Only metadata is reversible: storage purge is irreversible across downgrade.
    op.drop_table("scan_storage_retention_entries")
    op.execute("DROP FUNCTION guard_scan_retention_target()")
    op.execute("DROP TRIGGER scan_completion_anchor_guard ON scan_checking_events")
    op.execute("DROP FUNCTION guard_scan_completion_anchor()")
    op.drop_index("ix_scan_retention_completion", table_name="scan_checking_events")
