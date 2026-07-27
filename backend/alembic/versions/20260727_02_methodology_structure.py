"""Add version-scoped methodology structure.

Revision ID: 20260727_02
Revises: 20260719_01
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260727_02"
down_revision = "20260719_01"
branch_labels = None
depends_on = None
UUID = postgresql.UUID(as_uuid=True)

def _id():
    return sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))

def upgrade() -> None:
    grading = postgresql.ENUM("points", name="grading_mode", create_type=False)
    severity = postgresql.ENUM("low", "medium", "high", name="typical_error_severity", create_type=False)
    grading.create(op.get_bind())
    severity.create(op.get_bind())
    op.create_table("expected_solutions", _id(), sa.Column("task_version_id", UUID, nullable=False), sa.Column("solution_text", sa.Text(), nullable=False), sa.Column("final_answer", sa.Text()), sa.Column("solution_steps_json", postgresql.JSONB(), nullable=False), sa.ForeignKeyConstraint(["task_version_id"], ["task_versions.id"], name="fk_expected_solutions_task_version", ondelete="CASCADE"), sa.UniqueConstraint("task_version_id", name="uq_expected_solutions_task_version"))
    op.create_table("rubrics", _id(), sa.Column("task_version_id", UUID, nullable=False), sa.Column("max_score", sa.Numeric(), nullable=False), sa.Column("grading_mode", grading, nullable=False), sa.Column("notes", sa.Text()), sa.ForeignKeyConstraint(["task_version_id"], ["task_versions.id"], name="fk_rubrics_task_version", ondelete="CASCADE"), sa.UniqueConstraint("task_version_id", name="uq_rubrics_task_version"), sa.CheckConstraint("max_score >= 0", name="ck_rubrics_max_score_nonnegative"))
    op.create_table("rubric_items", _id(), sa.Column("rubric_id", UUID, nullable=False), sa.Column("criterion", sa.Text(), nullable=False), sa.Column("max_points", sa.Numeric(), nullable=False), sa.Column("required", sa.Boolean(), nullable=False), sa.Column("common_failure", sa.Text()), sa.Column("order_index", sa.Integer(), nullable=False), sa.ForeignKeyConstraint(["rubric_id"], ["rubrics.id"], name="fk_rubric_items_rubric", ondelete="CASCADE"), sa.UniqueConstraint("rubric_id", "order_index", name="uq_rubric_items_rubric_order"), sa.CheckConstraint("max_points > 0", name="ck_rubric_items_max_points_positive"), sa.CheckConstraint("order_index >= 0", name="ck_rubric_items_order_nonnegative"))
    op.create_table("accepted_answers", _id(), sa.Column("task_version_id", UUID, nullable=False), sa.Column("answer_value", sa.Text(), nullable=False), sa.Column("tolerance", sa.Numeric()), sa.Column("unit", sa.Text()), sa.Column("normalization_rule", sa.Text()), sa.ForeignKeyConstraint(["task_version_id"], ["task_versions.id"], name="fk_accepted_answers_task_version", ondelete="CASCADE"), sa.CheckConstraint("tolerance IS NULL OR tolerance >= 0", name="ck_accepted_answers_tolerance_nonnegative"))
    op.create_table("typical_errors", _id(), sa.Column("skill_id", UUID, nullable=False), sa.Column("code", sa.Text(), nullable=False), sa.Column("title", sa.Text(), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("severity", severity, nullable=False), sa.Column("remediation_hint", sa.Text()), sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name="fk_typical_errors_skill", ondelete="RESTRICT"), sa.UniqueConstraint("skill_id", "code", name="uq_typical_errors_skill_code"))
    op.create_table("task_error_links", _id(), sa.Column("task_version_id", UUID, nullable=False), sa.Column("typical_error_id", UUID, nullable=False), sa.Column("detection_hint", sa.Text()), sa.ForeignKeyConstraint(["task_version_id"], ["task_versions.id"], name="fk_task_error_links_task_version", ondelete="CASCADE"), sa.ForeignKeyConstraint(["typical_error_id"], ["typical_errors.id"], name="fk_task_error_links_typical_error", ondelete="CASCADE"), sa.UniqueConstraint("task_version_id", "typical_error_id", name="uq_task_error_links_version_error"))
    op.create_table("hints", _id(), sa.Column("task_version_id", UUID, nullable=False), sa.Column("level", sa.Integer(), nullable=False), sa.Column("hint_text", sa.Text(), nullable=False), sa.ForeignKeyConstraint(["task_version_id"], ["task_versions.id"], name="fk_hints_task_version", ondelete="CASCADE"), sa.UniqueConstraint("task_version_id", "level", name="uq_hints_version_level"), sa.CheckConstraint("level > 0", name="ck_hints_level_positive"))
    for name, table, cols in (("ix_rubric_items_rubric_id","rubric_items",["rubric_id"]),("ix_accepted_answers_task_version_id","accepted_answers",["task_version_id"]),("ix_typical_errors_skill_id","typical_errors",["skill_id"]),("ix_task_error_links_task_version_id","task_error_links",["task_version_id"]),("ix_task_error_links_typical_error_id","task_error_links",["typical_error_id"]),("ix_hints_task_version_id","hints",["task_version_id"])):
        op.create_index(name, table, cols)

def downgrade() -> None:
    for table in ("hints", "task_error_links", "typical_errors", "accepted_answers", "rubric_items", "rubrics", "expected_solutions"):
        op.drop_table(table)
    postgresql.ENUM(name="typical_error_severity").drop(op.get_bind())
    postgresql.ENUM(name="grading_mode").drop(op.get_bind())
