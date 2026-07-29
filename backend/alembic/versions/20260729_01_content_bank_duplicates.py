"""Add pg_trgm support for Content Bank duplicate candidates.

Revision ID: 20260729_01
Revises: 20260728_02

Downgrade intentionally retains pg_trgm: the extension is a shared database
capability and may have existed before this revision.
"""
from alembic import op

revision="20260729_01"
down_revision="20260728_02"
branch_labels=None
depends_on=None

INDEX_NAME="ix_task_versions_statement_trgm_gin"

def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(f"CREATE INDEX {INDEX_NAME} ON task_versions USING gin (statement gin_trgm_ops)")

def downgrade() -> None:
    op.drop_index(INDEX_NAME,table_name="task_versions",postgresql_using="gin")
    # Do not DROP EXTENSION pg_trgm; see module docstring.
