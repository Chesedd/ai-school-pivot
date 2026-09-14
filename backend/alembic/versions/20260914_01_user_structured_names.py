"""Add nullable structured user names without interpreting legacy display names.

Revision ID: 20260914_01
Revises: 20260911_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260914_01"
down_revision = "20260911_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(100), nullable=True))
    op.create_check_constraint("ck_users_first_name_valid", "users", "first_name IS NULL OR (first_name = btrim(first_name) AND char_length(first_name) BETWEEN 1 AND 100)")
    op.create_check_constraint("ck_users_last_name_valid", "users", "last_name IS NULL OR (last_name = btrim(last_name) AND char_length(last_name) BETWEEN 1 AND 100)")


def downgrade() -> None:
    op.drop_constraint("ck_users_last_name_valid", "users", type_="check")
    op.drop_constraint("ck_users_first_name_valid", "users", type_="check")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
