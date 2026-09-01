"""Persist administrative curriculum proposal resolution.

Revision ID: 20260901_02
Revises: 20260901_01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260901_02"
down_revision = "20260901_01"
branch_labels = None
depends_on = None

TABLES = ("subjects", "grades", "topics", "subtopics", "skills")


def upgrade():
    for table in TABLES:
        op.add_column(table, sa.Column("resolved_by", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("resolution_reason", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("replacement_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(f"fk_{table}_resolved_by_users", table, "users", ["resolved_by"], ["id"], ondelete="RESTRICT", onupdate="RESTRICT")
        op.create_foreign_key(f"fk_{table}_replacement_id_{table}", table, table, ["replacement_id"], ["id"], ondelete="RESTRICT")
        op.create_check_constraint(f"ck_{table}_resolution_pair", table, "(resolved_by IS NULL) = (resolved_at IS NULL)")
        op.create_check_constraint(f"ck_{table}_provisional_unresolved", table, "status <> 'provisional' OR (proposed_by IS NOT NULL AND resolved_by IS NULL AND resolved_at IS NULL AND replacement_id IS NULL AND resolution_reason IS NULL)")
        op.create_check_constraint(f"ck_{table}_replacement_resolution", table, f"replacement_id IS NULL OR (status = 'deprecated' AND resolved_by IS NOT NULL AND replacement_id <> id)")
        op.create_check_constraint(f"ck_{table}_resolution_reason", table, "resolution_reason IS NULL OR (char_length(btrim(resolution_reason)) BETWEEN 1 AND 500)")


def downgrade():
    for table in reversed(TABLES):
        for constraint in ("resolution_reason", "replacement_resolution", "provisional_unresolved", "resolution_pair"):
            op.drop_constraint(f"ck_{table}_{constraint}", table, type_="check")
        op.drop_constraint(f"fk_{table}_replacement_id_{table}", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_resolved_by_users", table, type_="foreignkey")
        for column in ("replacement_id", "resolution_reason", "resolved_at", "resolved_by"):
            op.drop_column(table, column)
