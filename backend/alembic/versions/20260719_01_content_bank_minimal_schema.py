"""Create the minimal Content Bank catalog and task schema.

Revision ID: 20260719_01
Revises:
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260719_01"
down_revision = None
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True)


def _id_column() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    task_version_status = postgresql.ENUM("draft", "review", "approved", "archived", name="task_version_status", create_type=False)
    task_type = postgresql.ENUM("test", "calculation", "problem", "open_question", "essay", name="task_type", create_type=False)
    answer_format = postgresql.ENUM("single_choice", "multiple_choice", "short_text", "number", "expression", "long_text", name="answer_format", create_type=False)
    difficulty_level = postgresql.ENUM("basic", "standard", "advanced", name="difficulty_level", create_type=False)
    for enum in (task_version_status, task_type, answer_format, difficulty_level):
        enum.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "subjects",
        _id_column(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_subjects"),
        sa.UniqueConstraint("code", name="uq_subjects_code"),
    )
    op.create_table(
        "grades",
        _id_column(),
        sa.Column("number", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_grades"),
        sa.UniqueConstraint("number", name="uq_grades_number"),
        sa.CheckConstraint("number BETWEEN 1 AND 11", name="ck_grades_number_range"),
    )
    op.create_table(
        "topics",
        _id_column(),
        sa.Column("subject_id", UUID, nullable=False),
        sa.Column("grade_id", UUID, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_topics"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], name="fk_topics_subject_id_subjects", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grade_id"], ["grades.id"], name="fk_topics_grade_id_grades", ondelete="RESTRICT"),
        sa.UniqueConstraint("subject_id", "grade_id", "code", name="uq_topics_subject_grade_code"),
    )
    op.create_table(
        "subtopics",
        _id_column(),
        sa.Column("topic_id", UUID, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_subtopics"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], name="fk_subtopics_topic_id_topics", ondelete="RESTRICT"),
        sa.UniqueConstraint("topic_id", "code", name="uq_subtopics_topic_code"),
    )
    op.create_table(
        "skills",
        _id_column(),
        sa.Column("subtopic_id", UUID, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_skills"),
        sa.ForeignKeyConstraint(["subtopic_id"], ["subtopics.id"], name="fk_skills_subtopic_id_subtopics", ondelete="RESTRICT"),
        sa.UniqueConstraint("subtopic_id", "code", name="uq_skills_subtopic_code"),
    )
    op.create_table(
        "tasks",
        _id_column(),
        sa.Column("subject_id", UUID, nullable=False),
        sa.Column("grade_id", UUID, nullable=False),
        sa.Column("topic_id", UUID, nullable=False),
        sa.Column("subtopic_id", UUID, nullable=True),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("archived_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], name="fk_tasks_subject_id_subjects", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grade_id"], ["grades.id"], name="fk_tasks_grade_id_grades", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], name="fk_tasks_topic_id_topics", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subtopic_id"], ["subtopics.id"], name="fk_tasks_subtopic_id_subtopics", ondelete="RESTRICT"),
    )
    op.create_table(
        "task_versions",
        _id_column(),
        sa.Column("task_id", UUID, nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("task_type", task_type, nullable=False),
        sa.Column("answer_format", answer_format, nullable=False),
        sa.Column("difficulty", difficulty_level, nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("status", task_version_status, nullable=False, server_default=sa.text("'draft'::task_version_status")),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("approved_by", UUID, nullable=True),
        sa.Column("approved_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_task_versions"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_task_versions_task_id_tasks", ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", "version_no", name="uq_task_versions_task_version_no"),
        sa.CheckConstraint("version_no > 0", name="ck_task_versions_version_no_positive"),
        sa.CheckConstraint("(approved_at IS NULL) = (approved_by IS NULL)", name="ck_task_versions_approval_pair"),
    )
    op.create_table(
        "task_skill_links",
        _id_column(),
        sa.Column("task_version_id", UUID, nullable=False),
        sa.Column("skill_id", UUID, nullable=False),
        sa.Column("weight", sa.Numeric(5, 4), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_skill_links"),
        sa.ForeignKeyConstraint(["task_version_id"], ["task_versions.id"], name="fk_task_skill_links_version_id_versions", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name="fk_task_skill_links_skill_id_skills", ondelete="RESTRICT"),
        sa.UniqueConstraint("task_version_id", "skill_id", name="uq_task_skill_links_version_skill"),
        sa.CheckConstraint("weight > 0 AND weight <= 1", name="ck_task_skill_links_weight_range"),
    )

    for name, table, columns in (
        ("ix_topics_subject_id", "topics", ["subject_id"]),
        ("ix_topics_grade_id", "topics", ["grade_id"]),
        ("ix_subtopics_topic_id", "subtopics", ["topic_id"]),
        ("ix_skills_subtopic_id", "skills", ["subtopic_id"]),
        ("ix_tasks_subject_id", "tasks", ["subject_id"]),
        ("ix_tasks_grade_id", "tasks", ["grade_id"]),
        ("ix_tasks_topic_id", "tasks", ["topic_id"]),
        ("ix_tasks_subtopic_id", "tasks", ["subtopic_id"]),
        ("ix_tasks_subject_grade_topic_subtopic", "tasks", ["subject_id", "grade_id", "topic_id", "subtopic_id"]),
        ("ix_tasks_created_at", "tasks", ["created_at"]),
        ("ix_tasks_archived_at", "tasks", ["archived_at"]),
        ("ix_task_versions_task_id_status", "task_versions", ["task_id", "status"]),
        ("ix_task_versions_status", "task_versions", ["status"]),
        ("ix_task_skill_links_skill_id", "task_skill_links", ["skill_id"]),
    ):
        op.create_index(name, table, columns)
    op.create_index(
        "uq_task_skill_links_one_primary_per_version",
        "task_skill_links",
        ["task_version_id"],
        unique=True,
        postgresql_where=sa.text("is_primary IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("uq_task_skill_links_one_primary_per_version", table_name="task_skill_links")
    for name, table in (
        ("ix_task_skill_links_skill_id", "task_skill_links"),
        ("ix_task_versions_status", "task_versions"),
        ("ix_task_versions_task_id_status", "task_versions"),
        ("ix_tasks_archived_at", "tasks"),
        ("ix_tasks_created_at", "tasks"),
        ("ix_tasks_subject_grade_topic_subtopic", "tasks"),
        ("ix_tasks_subtopic_id", "tasks"),
        ("ix_tasks_topic_id", "tasks"),
        ("ix_tasks_grade_id", "tasks"),
        ("ix_tasks_subject_id", "tasks"),
        ("ix_skills_subtopic_id", "skills"),
        ("ix_subtopics_topic_id", "subtopics"),
        ("ix_topics_grade_id", "topics"),
        ("ix_topics_subject_id", "topics"),
    ):
        op.drop_index(name, table_name=table)
    for table in ("task_skill_links", "task_versions", "tasks", "skills", "subtopics", "topics", "grades", "subjects"):
        op.drop_table(table)
    bind = op.get_bind()
    for enum in (
        postgresql.ENUM(name="difficulty_level", create_type=False),
        postgresql.ENUM(name="answer_format", create_type=False),
        postgresql.ENUM(name="task_type", create_type=False),
        postgresql.ENUM(name="task_version_status", create_type=False),
    ):
        enum.drop(bind, checkfirst=False)
