"""Migrate task difficulty enum to a numeric 1-100 scale.

Revision ID: 20260730_01
Revises: 20260729_01
"""
from alembic import op

revision = "20260730_01"
down_revision = "20260729_01"
branch_labels = None
depends_on = None
CONSTRAINT = "ck_task_versions_difficulty_range"


def upgrade() -> None:
    op.execute("ALTER TABLE task_versions ADD COLUMN difficulty_numeric SMALLINT")
    op.execute("UPDATE task_versions SET difficulty_numeric = CASE difficulty::text WHEN 'basic' THEN 25 WHEN 'standard' THEN 50 WHEN 'advanced' THEN 75 END")
    op.execute("ALTER TABLE task_versions ALTER COLUMN difficulty_numeric SET NOT NULL")
    op.drop_column("task_versions", "difficulty")
    op.alter_column("task_versions", "difficulty_numeric", new_column_name="difficulty")
    op.create_check_constraint(CONSTRAINT, "task_versions", "difficulty BETWEEN 1 AND 100")
    op.execute("DROP TYPE difficulty_level")


def downgrade() -> None:
    op.execute("CREATE TYPE difficulty_level AS ENUM ('basic', 'standard', 'advanced')")
    op.execute("ALTER TABLE task_versions ADD COLUMN difficulty_enum difficulty_level")
    op.execute("UPDATE task_versions SET difficulty_enum = (CASE WHEN difficulty BETWEEN 1 AND 33 THEN 'basic' WHEN difficulty BETWEEN 34 AND 66 THEN 'standard' ELSE 'advanced' END)::difficulty_level")
    op.execute("ALTER TABLE task_versions ALTER COLUMN difficulty_enum SET NOT NULL")
    op.drop_constraint(CONSTRAINT, "task_versions", type_="check")
    op.drop_column("task_versions", "difficulty")
    op.alter_column("task_versions", "difficulty_enum", new_column_name="difficulty")
