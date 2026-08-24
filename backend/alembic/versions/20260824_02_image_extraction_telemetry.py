"""Store normalized extraction provider telemetry on existing checkpoints.

Revision ID: 20260824_02
Revises: 20260824_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260824_02"
down_revision = "20260824_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("image_solving_checkpoints", sa.Column("provider_id", sa.String(128)))
    op.add_column("image_solving_checkpoints", sa.Column("model_id", sa.String(256)))
    op.add_column("image_solving_checkpoints", sa.Column("provider_request_id", sa.String(256)))
    op.add_column("image_solving_checkpoints", sa.Column("input_tokens", sa.Integer()))
    op.add_column("image_solving_checkpoints", sa.Column("output_tokens", sa.Integer()))
    op.add_column("image_solving_checkpoints", sa.Column("cost_amount", sa.Numeric(20, 8)))
    op.add_column("image_solving_checkpoints", sa.Column("currency", sa.String(3)))
    op.create_check_constraint("ck_image_solving_checkpoint_usage", "image_solving_checkpoints",
        "(input_tokens IS NULL OR input_tokens >= 0) AND (output_tokens IS NULL OR output_tokens >= 0) AND (cost_amount IS NULL OR cost_amount >= 0)")


def downgrade():
    op.drop_constraint("ck_image_solving_checkpoint_usage", "image_solving_checkpoints", type_="check")
    for name in ("currency", "cost_amount", "output_tokens", "input_tokens",
                 "provider_request_id", "model_id", "provider_id"):
        op.drop_column("image_solving_checkpoints", name)
