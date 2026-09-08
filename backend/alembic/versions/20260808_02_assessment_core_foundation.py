"""Add the Phase 3.1 Assessment Core PostgreSQL foundation.

Revision ID: 20260808_02
Revises: 20260808_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260808_02"
down_revision = "20260808_01"
branch_labels = depends_on = None

UUID = postgresql.UUID(as_uuid=True)
CLOCK = sa.text("clock_timestamp()")


def id_column() -> sa.Column:
    return sa.Column(
        "id", UUID, server_default=sa.text("gen_random_uuid()"), primary_key=True
    )


def upgrade() -> None:
    assessment_status = postgresql.ENUM(
        "draft", "published", name="assessment_status", create_type=False
    )
    assignment_status = postgresql.ENUM(
        "open", "closed", name="assignment_status", create_type=False
    )
    submission_status = postgresql.ENUM(
        "draft", "submitted", name="submission_status", create_type=False
    )
    bind = op.get_bind()
    assessment_status.create(bind)
    assignment_status.create(bind)
    submission_status.create(bind)

    op.create_table(
        "class_groups",
        id_column(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("external_ref", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(name) BETWEEN 1 AND 120",
            name="ck_class_groups_name_valid",
        ),
    )
    op.create_index(
        "uq_class_groups_external_ref", "class_groups", ["external_ref"], unique=True,
        postgresql_where=sa.text("external_ref IS NOT NULL"),
    )
    op.create_index("ix_class_groups_active", "class_groups", ["archived_at", "id"])

    op.create_table(
        "students",
        id_column(),
        sa.Column("class_group_id", UUID, nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("external_ref", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["class_group_id"], ["class_groups.id"],
            name="fk_students_class_group_id_class_groups",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.CheckConstraint(
            "display_name = btrim(display_name) AND char_length(display_name) BETWEEN 1 AND 120",
            name="ck_students_display_name_valid",
        ),
    )
    op.create_index(
        "uq_students_group_external_ref", "students", ["class_group_id", "external_ref"],
        unique=True, postgresql_where=sa.text("external_ref IS NOT NULL"),
    )
    op.create_index(
        "ix_students_group_active", "students", ["class_group_id", "archived_at", "id"]
    )

    op.create_table(
        "assessments",
        id_column(),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column(
            "status", assessment_status,
            server_default=sa.text("'draft'::assessment_status"), nullable=False,
        ),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("published_by", UUID),
        sa.CheckConstraint(
            "title = btrim(title) AND char_length(title) BETWEEN 1 AND 200",
            name="ck_assessments_title_valid",
        ),
        sa.CheckConstraint(
            "description IS NULL OR char_length(description) <= 4000",
            name="ck_assessments_description_length",
        ),
        sa.CheckConstraint(
            "(published_at IS NULL) = (published_by IS NULL)",
            name="ck_assessments_publication_pair",
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND published_at IS NULL) OR "
            "(status = 'published' AND published_at IS NOT NULL)",
            name="ck_assessments_status_publication",
        ),
    )
    op.create_index(
        "ix_assessments_status_created", "assessments",
        ["status", sa.text("created_at DESC"), "id"],
    )

    op.create_table(
        "assessment_variants",
        id_column(),
        sa.Column("assessment_id", UUID, nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["assessments.id"],
            name="fk_assessment_variants_assessment_id_assessments",
            ondelete="CASCADE", onupdate="RESTRICT",
        ),
        sa.UniqueConstraint(
            "assessment_id", "position", name="uq_assessment_variants_assessment_position"
        ),
        sa.UniqueConstraint(
            "assessment_id", "name", name="uq_assessment_variants_assessment_name"
        ),
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(name) BETWEEN 1 AND 80",
            name="ck_assessment_variants_name_valid",
        ),
        sa.CheckConstraint("position > 0", name="ck_assessment_variants_position_positive"),
    )

    op.create_table(
        "assessment_items",
        id_column(),
        sa.Column("variant_id", UUID, nullable=False),
        sa.Column("task_version_id", UUID, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("points", sa.Numeric(8, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.ForeignKeyConstraint(
            ["variant_id"], ["assessment_variants.id"],
            name="fk_assessment_items_variant_id_variants",
            ondelete="CASCADE", onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_version_id"], ["task_versions.id"],
            name="fk_assessment_items_task_version_id_versions",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.UniqueConstraint("variant_id", "position", name="uq_assessment_items_variant_position"),
        sa.UniqueConstraint(
            "variant_id", "task_version_id", name="uq_assessment_items_variant_task_version"
        ),
        sa.CheckConstraint("position > 0", name="ck_assessment_items_position_positive"),
        sa.CheckConstraint(
            "points > 0 AND points <= 999999.99", name="ck_assessment_items_points_range"
        ),
    )
    op.create_index("ix_assessment_items_task_version_id", "assessment_items", ["task_version_id"])

    op.create_table(
        "assignments",
        id_column(),
        sa.Column("assessment_id", UUID, nullable=False),
        sa.Column("class_group_id", UUID, nullable=False),
        sa.Column(
            "status", assignment_status,
            server_default=sa.text("'open'::assignment_status"), nullable=False,
        ),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_attempts", sa.SmallInteger, server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by", UUID),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["assessments.id"],
            name="fk_assignments_assessment_id_assessments",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["class_group_id"], ["class_groups.id"],
            name="fk_assignments_class_group_id_class_groups",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.CheckConstraint("start_at < due_at", name="ck_assignments_window"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 100", name="ck_assignments_max_attempts"),
        sa.CheckConstraint(
            "(status = 'open' AND closed_at IS NULL AND closed_by IS NULL) OR "
            "(status = 'closed' AND closed_at IS NOT NULL AND closed_by IS NOT NULL)",
            name="ck_assignments_status_closed",
        ),
    )
    op.create_index("ix_assignments_assessment_id", "assignments", ["assessment_id"])
    op.create_index(
        "ix_assignments_group_status_window", "assignments",
        ["class_group_id", "status", "start_at", "due_at"],
    )
    op.create_index("ix_assignments_status_due_at", "assignments", ["status", "due_at"])

    op.create_table(
        "assignment_participants",
        id_column(),
        sa.Column("assignment_id", UUID, nullable=False),
        sa.Column("student_id", UUID, nullable=False),
        sa.Column("assigned_variant_id", UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.Column("variant_assigned_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["assignments.id"],
            name="fk_assignment_participants_assignment_id_assignments",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["students.id"],
            name="fk_assignment_participants_student_id_students",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_variant_id"], ["assessment_variants.id"],
            name="fk_assignment_participants_variant_id_variants",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.UniqueConstraint(
            "assignment_id", "student_id",
            name="uq_assignment_participants_assignment_student",
        ),
        sa.CheckConstraint(
            "(assigned_variant_id IS NULL) = (variant_assigned_at IS NULL)",
            name="ck_assignment_participants_variant_pair",
        ),
    )
    op.create_index(
        "ix_assignment_participants_student_assignment", "assignment_participants",
        ["student_id", "assignment_id"],
    )
    op.create_index(
        "ix_assignment_participants_assigned_variant", "assignment_participants",
        ["assigned_variant_id"],
    )

    op.create_table(
        "student_submissions",
        id_column(),
        sa.Column("assignment_participant_id", UUID, nullable=False),
        sa.Column("attempt_no", sa.SmallInteger, nullable=False),
        sa.Column(
            "status", submission_status,
            server_default=sa.text("'draft'::submission_status"), nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["assignment_participant_id"], ["assignment_participants.id"],
            name="fk_student_submissions_participant_id_participants",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.UniqueConstraint(
            "assignment_participant_id", "attempt_no",
            name="uq_student_submissions_participant_attempt",
        ),
        sa.CheckConstraint("attempt_no > 0", name="ck_student_submissions_attempt_positive"),
        sa.CheckConstraint(
            "(status = 'draft' AND submitted_at IS NULL) OR "
            "(status = 'submitted' AND submitted_at IS NOT NULL)",
            name="ck_student_submissions_status_submitted",
        ),
    )
    op.create_index(
        "uq_student_submissions_one_draft", "student_submissions",
        ["assignment_participant_id"], unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.create_index(
        "ix_student_submissions_participant_status", "student_submissions",
        ["assignment_participant_id", "status"],
    )
    op.create_index(
        "ix_student_submissions_status_started", "student_submissions", ["status", "started_at"]
    )

    op.create_table(
        "student_answers",
        id_column(),
        sa.Column("submission_id", UUID, nullable=False),
        sa.Column("assessment_item_id", UUID, nullable=False),
        sa.Column("raw_answer", postgresql.JSONB, nullable=False),
        sa.Column("normalized_answer", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["student_submissions.id"],
            name="fk_student_answers_submission_id_submissions",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_item_id"], ["assessment_items.id"],
            name="fk_student_answers_item_id_items",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.UniqueConstraint(
            "submission_id", "assessment_item_id", name="uq_student_answers_submission_item"
        ),
    )
    op.create_index(
        "ix_student_answers_assessment_item_id", "student_answers", ["assessment_item_id"]
    )

    op.create_table(
        "assessment_idempotency_keys",
        id_column(),
        sa.Column("assignment_participant_id", UUID, nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("submission_id", UUID, nullable=False),
        sa.Column("http_status", sa.SmallInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.ForeignKeyConstraint(
            ["assignment_participant_id"], ["assignment_participants.id"],
            name="fk_assessment_idempotency_participant_id_participants",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["student_submissions.id"],
            name="fk_assessment_idempotency_submission_id_submissions",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.UniqueConstraint(
            "assignment_participant_id", "key",
            name="uq_assessment_idempotency_participant_key",
        ),
        sa.CheckConstraint(
            "operation IN ('start','submit')", name="ck_assessment_idempotency_operation"
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name="ck_assessment_idempotency_request_hash"
        ),
        sa.CheckConstraint("http_status IN (200,201)", name="ck_assessment_idempotency_http_status"),
    )
    op.create_index(
        "ix_assessment_idempotency_submission_id", "assessment_idempotency_keys", ["submission_id"]
    )

    op.create_table(
        "assessment_audit_log",
        id_column(),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=CLOCK, nullable=False),
        sa.Column("details", postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint(
            "aggregate_type IN ('assessment','assignment','submission')",
            name="ck_assessment_audit_aggregate_type",
        ),
        sa.CheckConstraint(
            "actor_type IN ('teacher','student','system')",
            name="ck_assessment_audit_actor_type",
        ),
        sa.CheckConstraint(
            "event_type IN ('assessment_created','assessment_metadata_updated',"
            "'variant_created','variant_deleted','item_added','item_removed',"
            "'items_reordered','item_points_changed','assessment_published',"
            "'assignment_created','assignment_closed','variant_assigned',"
            "'submission_started','answer_saved','answer_deleted','submission_submitted')",
            name="ck_assessment_audit_event_type",
        ),
    )
    op.create_index(
        "ix_assessment_audit_aggregate_occurred", "assessment_audit_log",
        ["aggregate_type", "aggregate_id", sa.text("occurred_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    for table in (
        "assessment_audit_log", "assessment_idempotency_keys", "student_answers",
        "student_submissions", "assignment_participants", "assignments",
        "assessment_items", "assessment_variants", "assessments", "students",
        "class_groups",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    postgresql.ENUM(name="submission_status").drop(bind)
    postgresql.ENUM(name="assignment_status").drop(bind)
    postgresql.ENUM(name="assessment_status").drop(bind)
