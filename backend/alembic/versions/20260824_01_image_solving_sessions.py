"""Add dedicated image-solving sessions and checkpoints.

Revision ID: 20260824_01
Revises: 20260823_06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "20260824_01"
down_revision = "20260823_06"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("image_solving_sessions", sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False), sa.Column("owner_id", sa.Uuid(), nullable=False), sa.Column("input_artifact_id", sa.Uuid(), nullable=False), sa.Column("status", sa.String(16), server_default="created", nullable=False), sa.Column("failure_code", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False), sa.ForeignKeyConstraint(["input_artifact_id"], ["input_artifacts.id"], ondelete="RESTRICT"), sa.CheckConstraint("status IN ('created','extracting','extracted','solving','solved','validated','failed')", name="ck_image_solving_sessions_status"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_image_solving_sessions_owner_created", "image_solving_sessions", ["owner_id", "created_at"])
    op.create_table("image_solving_checkpoints", sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False), sa.Column("session_id", sa.Uuid(), nullable=False), sa.Column("stage", sa.String(16), nullable=False), sa.Column("payload", postgresql.JSONB(), nullable=False), sa.Column("fingerprint", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False), sa.ForeignKeyConstraint(["session_id"], ["image_solving_sessions.id"], ondelete="RESTRICT"), sa.CheckConstraint("stage IN ('extraction','solver','validation')", name="ck_image_solving_checkpoints_stage"), sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="ck_image_solving_checkpoints_fingerprint"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("session_id", "stage", name="uq_image_solving_checkpoints_stage"))

def downgrade():
    op.drop_table("image_solving_checkpoints")
    op.drop_index("ix_image_solving_sessions_owner_created", table_name="image_solving_sessions")
    op.drop_table("image_solving_sessions")
