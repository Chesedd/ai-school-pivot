"""Minimal image attachments outside snapshots.

Revision ID: 20260821_01
Revises: 20260820_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="20260821_01"; down_revision="20260820_01"; branch_labels=None; depends_on=None

def upgrade():
    op.create_table("attachments",sa.Column("id",postgresql.UUID(as_uuid=True),server_default=sa.text("gen_random_uuid()"),primary_key=True),sa.Column("filename",sa.String(255),nullable=False),sa.Column("mime_type",sa.String(64),nullable=False),sa.Column("storage_reference",sa.String(255),nullable=False,unique=True),sa.Column("size_bytes",sa.Integer(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("CURRENT_TIMESTAMP"),nullable=False),sa.CheckConstraint("mime_type IN ('image/png','image/jpeg','image/webp')",name="ck_attachments_image_mime"),sa.CheckConstraint("size_bytes > 0",name="ck_attachments_size_positive"))
    op.create_table("task_version_attachments",sa.Column("task_version_id",postgresql.UUID(as_uuid=True),sa.ForeignKey("task_versions.id",ondelete="CASCADE"),primary_key=True),sa.Column("attachment_id",postgresql.UUID(as_uuid=True),sa.ForeignKey("attachments.id",ondelete="CASCADE"),primary_key=True),sa.Column("role",sa.String(40),nullable=False),sa.CheckConstraint("role IN ('statement','description','additional_material','solution_explanation','methodological_material')",name="ck_task_attachment_role"))
    op.create_table("student_answer_attachments",sa.Column("student_answer_id",postgresql.UUID(as_uuid=True),sa.ForeignKey("student_answers.id",ondelete="CASCADE"),primary_key=True),sa.Column("attachment_id",postgresql.UUID(as_uuid=True),sa.ForeignKey("attachments.id",ondelete="CASCADE"),primary_key=True))

def downgrade():
    op.drop_table("student_answer_attachments");op.drop_table("task_version_attachments");op.drop_table("attachments")
