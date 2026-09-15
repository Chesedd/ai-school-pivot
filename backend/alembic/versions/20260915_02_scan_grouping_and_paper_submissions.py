"""Add teacher grouping revisions and paper submissions.

Revision ID: 20260915_02
Revises: 20260915_01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260915_02"
down_revision = "20260915_01"
branch_labels = None
depends_on = None
u = postgresql.UUID(as_uuid=True)
now = sa.text("clock_timestamp()")


def upgrade():
    op.create_table(
        "scan_grouping_revisions",
        sa.Column(
            "id", u, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "batch_id",
            u,
            sa.ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("based_on_matching_revision", sa.Integer, nullable=False),
        sa.Column(
            "created_by_user_id",
            u,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "confirmed_by_user_id", u, sa.ForeignKey("users.id", ondelete="RESTRICT")
        ),
        sa.UniqueConstraint(
            "batch_id", "revision", name="uq_scan_grouping_revisions_revision"
        ),
        sa.CheckConstraint(
            "revision > 0 AND based_on_matching_revision > 0 AND row_version > 0",
            name="ck_scan_grouping_revisions_versions",
        ),
        sa.CheckConstraint(
            "status IN ('draft','confirmed','superseded')",
            name="ck_scan_grouping_revisions_status",
        ),
        sa.CheckConstraint(
            "(status='confirmed' AND confirmed_at IS NOT NULL AND confirmed_by_user_id IS NOT NULL) OR (status<>'confirmed' AND confirmed_at IS NULL AND confirmed_by_user_id IS NULL)",
            name="ck_scan_grouping_revisions_confirmation",
        ),
    )
    op.create_index(
        "uq_scan_grouping_revisions_one_draft",
        "scan_grouping_revisions",
        ["batch_id"],
        unique=True,
        postgresql_where=sa.text("status='draft'"),
    )
    op.create_table(
        "scan_grouping_entries",
        sa.Column(
            "id", u, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "grouping_revision_id",
            u,
            sa.ForeignKey("scan_grouping_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assignment_participant_id",
            u,
            sa.ForeignKey("assignment_participants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.UniqueConstraint(
            "id", "grouping_revision_id", name="uq_scan_grouping_entries_id_revision"
        ),
        sa.UniqueConstraint(
            "grouping_revision_id",
            "assignment_participant_id",
            name="uq_scan_grouping_entries_participant",
        ),
        sa.UniqueConstraint(
            "grouping_revision_id", "position", name="uq_scan_grouping_entries_position"
        ),
        sa.CheckConstraint("position >= 0", name="ck_scan_grouping_entries_position"),
    )
    op.create_table(
        "scan_grouping_pages",
        sa.Column("grouping_entry_id", u, nullable=False),
        sa.Column("grouping_revision_id", u, nullable=False),
        sa.Column(
            "scan_page_id",
            u,
            sa.ForeignKey("scan_pages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_order", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.PrimaryKeyConstraint(
            "grouping_entry_id", "grouping_revision_id", "scan_page_id"
        ),
        sa.ForeignKeyConstraint(
            ["grouping_entry_id", "grouping_revision_id"],
            ["scan_grouping_entries.id", "scan_grouping_entries.grouping_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "grouping_revision_id",
            "scan_page_id",
            name="uq_scan_grouping_pages_revision_page",
        ),
        sa.UniqueConstraint(
            "grouping_entry_id", "page_order", name="uq_scan_grouping_pages_order"
        ),
        sa.CheckConstraint("page_order >= 0", name="ck_scan_grouping_pages_order"),
    )
    op.create_table(
        "scan_grouping_unmatched_pages",
        sa.Column(
            "grouping_revision_id",
            u,
            sa.ForeignKey("scan_grouping_revisions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "scan_page_id",
            u,
            sa.ForeignKey("scan_pages.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("reason_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
    )
    op.create_table(
        "paper_submissions",
        sa.Column(
            "id", u, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "batch_id",
            u,
            sa.ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "grouping_revision_id",
            u,
            sa.ForeignKey("scan_grouping_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assignment_id",
            u,
            sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assignment_participant_id",
            u,
            sa.ForeignKey("assignment_participants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            u,
            sa.ForeignKey("students.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assigned_variant_id",
            u,
            sa.ForeignKey("assessment_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="ready_for_checking"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column(
            "created_by_user_id",
            u,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "batch_id",
            "assignment_participant_id",
            name="uq_paper_submissions_batch_participant",
        ),
        sa.UniqueConstraint(
            "grouping_revision_id",
            "assignment_participant_id",
            name="uq_paper_submissions_revision_participant",
        ),
        sa.CheckConstraint(
            "status='ready_for_checking'", name="ck_paper_submissions_status"
        ),
    )
    op.create_table(
        "paper_submission_pages",
        sa.Column(
            "paper_submission_id",
            u,
            sa.ForeignKey("paper_submissions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "scan_page_id",
            u,
            sa.ForeignKey("scan_pages.id", ondelete="RESTRICT"),
            primary_key=True,
            unique=True,
        ),
        sa.Column("page_order", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.UniqueConstraint(
            "paper_submission_id", "page_order", name="uq_paper_submission_pages_order"
        ),
        sa.CheckConstraint("page_order >= 0", name="ck_paper_submission_pages_order"),
    )
    op.drop_constraint(
        "ck_scan_checking_events_aggregate", "scan_checking_events", type_="check"
    )
    op.drop_constraint(
        "ck_scan_checking_events_type", "scan_checking_events", type_="check"
    )
    op.create_check_constraint(
        "ck_scan_checking_events_aggregate",
        "scan_checking_events",
        "aggregate_type IN ('batch','artifact','page','grouping','paper_submission')",
    )
    op.create_check_constraint(
        "ck_scan_checking_events_type",
        "scan_checking_events",
        "event_type IN ('batch.created','batch.cancelled','artifact.attached','artifact.extraction_status_changed','page.created','page.status_changed','grouping.draft_created','grouping.updated','grouping.confirmed','paper_submission.created')",
    )


def downgrade():
    op.drop_constraint(
        "ck_scan_checking_events_type", "scan_checking_events", type_="check"
    )
    op.drop_constraint(
        "ck_scan_checking_events_aggregate", "scan_checking_events", type_="check"
    )
    op.create_check_constraint(
        "ck_scan_checking_events_aggregate",
        "scan_checking_events",
        "aggregate_type IN ('batch','artifact','page')",
    )
    op.create_check_constraint(
        "ck_scan_checking_events_type",
        "scan_checking_events",
        "event_type IN ('batch.created','batch.cancelled','artifact.attached','artifact.extraction_status_changed','page.created','page.status_changed')",
    )
    for table in (
        "paper_submission_pages",
        "paper_submissions",
        "scan_grouping_unmatched_pages",
        "scan_grouping_pages",
        "scan_grouping_entries",
    ):
        op.drop_table(table)
    op.drop_index(
        "uq_scan_grouping_revisions_one_draft", table_name="scan_grouping_revisions"
    )
    op.drop_table("scan_grouping_revisions")
