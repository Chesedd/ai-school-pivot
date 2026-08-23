"""Add normalized provider cache usage dimensions.

Revision ID: 20260823_01
Revises: 20260821_03
"""
from alembic import op
import sqlalchemy as sa

revision="20260823_01"
down_revision="20260821_03"
branch_labels=None
depends_on=None


def upgrade():
    op.add_column("authoring_provider_attempts",sa.Column("cache_read_tokens",sa.Integer(),nullable=False,server_default="0"))
    op.add_column("authoring_provider_attempts",sa.Column("cache_write_tokens",sa.Integer(),nullable=False,server_default="0"))
    # Phase 4A.1 cached_tokens represented cache reads.
    op.execute("UPDATE authoring_provider_attempts SET cache_read_tokens=cached_tokens")
    op.create_check_constraint("ck_authoring_attempts_cache_dimensions","authoring_provider_attempts","cache_read_tokens BETWEEN 0 AND 10000000 AND cache_write_tokens BETWEEN 0 AND 10000000")


def downgrade():
    op.drop_constraint("ck_authoring_attempts_cache_dimensions","authoring_provider_attempts",type_="check")
    op.drop_column("authoring_provider_attempts","cache_write_tokens")
    op.drop_column("authoring_provider_attempts","cache_read_tokens")
