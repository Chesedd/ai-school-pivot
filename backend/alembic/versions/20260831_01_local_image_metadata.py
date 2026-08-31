"""Remove provider telemetry from locally resolved image metadata.

Revision ID: 20260831_01
Revises: 20260826_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260831_01"
down_revision = "20260826_01"
branch_labels = None
depends_on = None

_COLUMNS = ("provider_id", "model_id", "provider_request_id", "input_tokens",
    "output_tokens", "cost_amount", "currency")


def upgrade():
    op.drop_constraint("ck_image_solving_metadata_usage",
        "image_solving_metadata_recommendations", type_="check")
    for column in _COLUMNS:
        op.drop_column("image_solving_metadata_recommendations", column)


def downgrade():
    table = "image_solving_metadata_recommendations"
    op.add_column(table, sa.Column("provider_id", sa.String(128)))
    op.add_column(table, sa.Column("model_id", sa.String(256)))
    op.add_column(table, sa.Column("provider_request_id", sa.String(256)))
    op.add_column(table, sa.Column("input_tokens", sa.Integer()))
    op.add_column(table, sa.Column("output_tokens", sa.Integer()))
    op.add_column(table, sa.Column("cost_amount", sa.Numeric(20, 8)))
    op.add_column(table, sa.Column("currency", sa.String(3)))
    op.create_check_constraint("ck_image_solving_metadata_usage", table,
        "(input_tokens IS NULL OR input_tokens >= 0) AND "
        "(output_tokens IS NULL OR output_tokens >= 0) AND "
        "(cost_amount IS NULL OR cost_amount >= 0)")
