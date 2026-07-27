"""Latest-version full-text search and update timestamps.

Revision ID: 20260727_04
Revises: 20260727_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20260727_04"
down_revision = "20260727_03"
branch_labels = None
depends_on = None

SEARCH_VECTOR_SQL = "setweight(to_tsvector('russian'::regconfig, COALESCE(title, '')), 'A') || setweight(to_tsvector('russian'::regconfig, COALESCE(statement, '')), 'B') || setweight(to_tsvector('russian'::regconfig, COALESCE(source, '')), 'C')"

def upgrade() -> None:
    op.add_column("tasks", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")))
    op.add_column("task_versions", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")))
    op.execute("UPDATE tasks SET updated_at = created_at")
    op.execute("UPDATE task_versions SET updated_at = created_at")
    op.alter_column("tasks", "updated_at", nullable=False)
    op.alter_column("task_versions", "updated_at", nullable=False)
    op.add_column("task_versions", sa.Column("search_vector", sa.dialects.postgresql.TSVECTOR(), sa.Computed(SEARCH_VECTOR_SQL, persisted=True), nullable=True))
    op.create_index("ix_task_versions_search_vector_gin", "task_versions", ["search_vector"], postgresql_using="gin")
    op.create_index("ix_tasks_updated_at", "tasks", ["updated_at"])

def downgrade() -> None:
    op.drop_index("ix_tasks_updated_at", table_name="tasks")
    op.drop_index("ix_task_versions_search_vector_gin", table_name="task_versions", postgresql_using="gin")
    op.drop_column("task_versions", "search_vector")
    op.drop_column("task_versions", "updated_at")
    op.drop_column("tasks", "updated_at")
