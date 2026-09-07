"""Admin-managed classroom foundation.

Revision ID: 20260907_01
Revises: 20260905_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260907_01"
down_revision = "20260905_01"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("class_groups", sa.Column("grade_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_class_groups_grade_id_grades", "class_groups", "grades", ["grade_id"], ["id"], ondelete="RESTRICT", onupdate="RESTRICT")
    op.create_table("class_group_teachers",
        sa.Column("class_group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("teacher_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("class_group_id", "teacher_user_id", name="pk_class_group_teachers"),
        sa.ForeignKeyConstraint(["class_group_id"], ["class_groups.id"], name="fk_class_group_teachers_class_group_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users.id"], name="fk_class_group_teachers_teacher_user_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], name="fk_class_group_teachers_assigned_by", ondelete="RESTRICT", onupdate="RESTRICT"))
    op.create_index("ix_class_group_teachers_teacher_class", "class_group_teachers", ["teacher_user_id", "class_group_id"])
    op.create_table("classroom_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False), sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_classroom_audit_log"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_classroom_audit_actor_user_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.CheckConstraint("aggregate_type IN ('class_group','student','teacher_membership')", name="ck_classroom_audit_aggregate_type"),
        sa.CheckConstraint("event_type IN ('class.created','class.updated','class.archived','teacher.assigned','teacher.unassigned','student.created','student.moved','student.archived')", name="ck_classroom_audit_event_type"))
    op.create_index("ix_classroom_audit_aggregate", "classroom_audit_log", ["aggregate_type", "aggregate_id", "occurred_at", "id"])
    op.create_index("ix_classroom_audit_actor", "classroom_audit_log", ["actor_user_id", "occurred_at"])

def downgrade():
    raise RuntimeError("Forward-only migration")
