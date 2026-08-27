"""Persist recommendations separately from image-solving checkpoints.

Revision ID: 20260826_01
Revises: 20260824_02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="20260826_01"
down_revision="20260824_02"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("image_solving_metadata_recommendations",
        sa.Column("id",postgresql.UUID(as_uuid=True),server_default=sa.text("gen_random_uuid()"),primary_key=True),
        sa.Column("session_id",postgresql.UUID(as_uuid=True),sa.ForeignKey("image_solving_sessions.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("payload",postgresql.JSONB(),nullable=False),sa.Column("catalog_fingerprint",sa.String(64),nullable=False),
        sa.Column("provider_id",sa.String(128)),sa.Column("model_id",sa.String(256)),sa.Column("provider_request_id",sa.String(256)),
        sa.Column("input_tokens",sa.Integer()),sa.Column("output_tokens",sa.Integer()),sa.Column("cost_amount",sa.Numeric(20,8)),sa.Column("currency",sa.String(3)),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("clock_timestamp()"),nullable=False),
        sa.UniqueConstraint("session_id",name="uq_image_solving_metadata_session"),
        sa.CheckConstraint("(input_tokens IS NULL OR input_tokens >= 0) AND (output_tokens IS NULL OR output_tokens >= 0) AND (cost_amount IS NULL OR cost_amount >= 0)",name="ck_image_solving_metadata_usage"))

def downgrade():
    op.drop_table("image_solving_metadata_recommendations")
