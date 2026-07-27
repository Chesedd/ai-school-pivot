"""Enforce one currently approved version per task.

Revision ID: 20260727_03
Revises: 20260727_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20260727_03"
down_revision = "20260727_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("uq_task_versions_one_approved_per_task", "task_versions", ["task_id"], unique=True,
        postgresql_where=sa.text("status = 'approved'"))


def downgrade() -> None:
    op.drop_index("uq_task_versions_one_approved_per_task", table_name="task_versions")
