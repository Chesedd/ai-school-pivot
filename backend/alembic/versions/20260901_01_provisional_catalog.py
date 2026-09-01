"""Add provisional curriculum catalog persistence.

Revision ID: 20260901_01
Revises: 20260831_02
"""

import re
import unicodedata

from alembic import op
import sqlalchemy as sa

revision = "20260901_01"
down_revision = "20260831_02"
branch_labels = None
depends_on = None

TABLES = ("subjects", "grades", "topics", "subtopics", "skills")


def normalized(value: str) -> str:
    return re.sub(
        r"[^\w]+",
        " ",
        unicodedata.normalize("NFKC", value).casefold().replace("ё", "е"),
    ).strip()


def upgrade():
    lifecycle = sa.Enum("provisional", "active", "deprecated", name="catalog_lifecycle")
    lifecycle.create(op.get_bind(), checkfirst=True)
    for table in TABLES:
        op.add_column(table, sa.Column("normalized_name", sa.Text(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "status",
                lifecycle,
                server_default=sa.text("'active'::catalog_lifecycle"),
                nullable=False,
            ),
        )
        op.add_column(table, sa.Column("proposed_by", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("replacement_id", sa.Uuid(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("clock_timestamp()"),
                nullable=False,
            ),
        )
        rows = op.get_bind().execute(sa.text(f"SELECT id, name FROM {table}")).all()
        for row in rows:
            op.get_bind().execute(
                sa.text(f"UPDATE {table} SET normalized_name=:name WHERE id=:id"),
                {"id": row.id, "name": normalized(row.name)},
            )
        op.alter_column(table, "normalized_name", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_proposed_by_users",
            table,
            "users",
            ["proposed_by"],
            ["id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        )
        op.create_foreign_key(
            f"fk_{table}_replacement_id_{table}",
            table,
            table,
            ["replacement_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_check_constraint(
            f"ck_{table}_provisional_proposer",
            table,
            "status <> 'provisional' OR proposed_by IS NOT NULL",
        )
        op.create_check_constraint(
            f"ck_{table}_replacement_not_self",
            table,
            "replacement_id IS NULL OR replacement_id <> id",
        )
    op.create_index(
        "uq_subjects_live_normalized_name",
        "subjects",
        ["normalized_name"],
        unique=True,
        postgresql_where=sa.text("status IN ('active','provisional')"),
    )
    op.drop_constraint("uq_grades_number", "grades", type_="unique")
    op.create_index(
        "uq_grades_live_number",
        "grades",
        ["number"],
        unique=True,
        postgresql_where=sa.text("status IN ('active','provisional')"),
    )
    op.create_index(
        "uq_topics_live_identity",
        "topics",
        ["subject_id", "grade_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("status IN ('active','provisional')"),
    )
    op.create_index(
        "uq_subtopics_live_identity",
        "subtopics",
        ["topic_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("status IN ('active','provisional')"),
    )
    op.create_index(
        "uq_skills_live_identity",
        "skills",
        ["subtopic_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("status IN ('active','provisional')"),
    )


def downgrade():
    op.drop_index("uq_grades_live_number", table_name="grades")
    op.create_unique_constraint("uq_grades_number", "grades", ["number"])
    for table, index in (
        ("skills", "uq_skills_live_identity"),
        ("subtopics", "uq_subtopics_live_identity"),
        ("topics", "uq_topics_live_identity"),
        ("subjects", "uq_subjects_live_normalized_name"),
    ):
        op.drop_index(index, table_name=table)
    for table in reversed(TABLES):
        op.drop_constraint(f"ck_{table}_replacement_not_self", table, type_="check")
        op.drop_constraint(f"ck_{table}_provisional_proposer", table, type_="check")
        op.drop_constraint(
            f"fk_{table}_replacement_id_{table}", table, type_="foreignkey"
        )
        op.drop_constraint(f"fk_{table}_proposed_by_users", table, type_="foreignkey")
        for column in (
            "updated_at",
            "replacement_id",
            "proposed_by",
            "status",
            "normalized_name",
        ):
            op.drop_column(table, column)
    sa.Enum(name="catalog_lifecycle").drop(op.get_bind(), checkfirst=True)
