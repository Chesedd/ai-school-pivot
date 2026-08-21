"""Remove storage used exclusively by the retired task import workflow.

Revision ID: 20260821_02
Revises: 20260821_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260821_02"
down_revision = "20260821_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("import_previews")


def downgrade() -> None:
    op.create_table(
        "import_previews",
        sa.Column("import_token", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rows", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("format IN ('csv','xlsx')", name="ck_import_previews_format"),
        sa.CheckConstraint("expires_at > created_at", name="ck_import_previews_expiry"),
        sa.CheckConstraint("committed_at IS NULL OR committed_at >= created_at", name="ck_import_previews_committed_at"),
    )
    op.create_index("ix_import_previews_expires_at", "import_previews", ["expires_at"])
