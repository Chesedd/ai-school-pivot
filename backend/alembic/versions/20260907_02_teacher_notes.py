"""Private teacher class and student notes.

Revision ID: 20260907_02
Revises: 20260907_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260907_02"
down_revision = "20260907_01"
branch_labels = None
depends_on = None


def upgrade():
    uuid = postgresql.UUID(as_uuid=True)
    timestamps = (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
    )
    op.create_table("class_notes",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("class_group_id", uuid, nullable=False),
        sa.Column("teacher_user_id", uuid, nullable=False),
        sa.Column("body", sa.Text(), nullable=False), *timestamps,
        sa.PrimaryKeyConstraint("id", name="pk_class_notes"),
        sa.ForeignKeyConstraint(["class_group_id"], ["class_groups.id"], name="fk_class_notes_class_group_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users.id"], name="fk_class_notes_teacher_user_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 12000", name="ck_class_notes_body_length"))
    op.create_index("ix_class_notes_class_teacher_created", "class_notes", ["class_group_id", "teacher_user_id", sa.text("created_at DESC"), "id"])
    op.create_index("ix_class_notes_teacher_created", "class_notes", ["teacher_user_id", sa.text("created_at DESC"), "id"])
    op.create_table("student_notes",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("student_id", uuid, nullable=False),
        sa.Column("class_group_id", uuid, nullable=False),
        sa.Column("teacher_user_id", uuid, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_student_notes"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], name="fk_student_notes_student_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["class_group_id"], ["class_groups.id"], name="fk_student_notes_class_group_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users.id"], name="fk_student_notes_teacher_user_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 12000", name="ck_student_notes_body_length"))
    op.create_index("ix_student_notes_class_student_teacher_created", "student_notes", ["class_group_id", "student_id", "teacher_user_id", sa.text("created_at DESC"), "id"])
    op.create_index("ix_student_notes_student_teacher_created", "student_notes", ["student_id", "teacher_user_id", sa.text("created_at DESC"), "id"])


def downgrade():
    raise RuntimeError("Forward-only migration")
