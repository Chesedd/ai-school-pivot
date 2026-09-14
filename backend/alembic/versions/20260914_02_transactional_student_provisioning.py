"""Allow student profiles to preserve the canonical account display name.

Revision ID: 20260914_02
Revises: 20260914_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260914_02"
down_revision = "20260914_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_students_display_name_valid", "students", type_="check")
    op.alter_column("students", "display_name", existing_type=sa.String(120), type_=sa.String(200), existing_nullable=False)
    op.create_check_constraint("ck_students_display_name_valid", "students", "display_name = btrim(display_name) AND char_length(display_name) BETWEEN 1 AND 200")


def downgrade() -> None:
    op.drop_constraint("ck_students_display_name_valid", "students", type_="check")
    op.alter_column("students", "display_name", existing_type=sa.String(200), type_=sa.String(120), existing_nullable=False)
    op.create_check_constraint("ck_students_display_name_valid", "students", "display_name = btrim(display_name) AND char_length(display_name) BETWEEN 1 AND 120")
