"""Generalize assessment execution persistence for remediation targets.

Revision ID: 20260907_04
Revises: 20260907_03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260907_04"
down_revision = "20260907_03"
branch_labels = None
depends_on = None


def _item_target(table: str) -> None:
    op.add_column(table, sa.Column("remediation_plan_item_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        f"fk_{table}_remediation_item", table, "remediation_plan_items",
        ["remediation_plan_item_id"], ["id"], ondelete="RESTRICT", onupdate="RESTRICT",
    )
    op.alter_column(table, "assessment_item_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.create_check_constraint(
        f"ck_{table}_execution_item_xor", table,
        "num_nonnulls(assessment_item_id, remediation_plan_item_id) = 1",
    )


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)

    op.add_column("student_submissions", sa.Column("remediation_plan_id", uuid, nullable=True))
    op.create_foreign_key(
        "fk_student_submissions_remediation_plan", "student_submissions", "remediation_plans",
        ["remediation_plan_id"], ["id"], ondelete="RESTRICT", onupdate="RESTRICT",
    )
    op.alter_column("student_submissions", "assignment_participant_id", existing_type=uuid, nullable=True)
    op.create_check_constraint(
        "ck_student_submissions_execution_target_xor", "student_submissions",
        "num_nonnulls(assignment_participant_id, remediation_plan_id) = 1",
    )
    op.drop_constraint("uq_student_submissions_participant_attempt", "student_submissions", type_="unique")
    op.create_index(
        "uq_student_submissions_participant_attempt", "student_submissions",
        ["assignment_participant_id", "attempt_no"], unique=True,
        postgresql_where=sa.text("assignment_participant_id IS NOT NULL"),
    )
    op.create_index(
        "uq_student_submissions_remediation_attempt", "student_submissions",
        ["remediation_plan_id", "attempt_no"], unique=True,
        postgresql_where=sa.text("remediation_plan_id IS NOT NULL"),
    )
    op.create_index("ix_student_submissions_remediation_status", "student_submissions", ["remediation_plan_id", "status"])

    _item_target("student_answers")
    op.drop_constraint("uq_student_answers_submission_item", "student_answers", type_="unique")
    op.create_index("uq_student_answers_submission_item", "student_answers", ["submission_id", "assessment_item_id"], unique=True, postgresql_where=sa.text("assessment_item_id IS NOT NULL"))
    op.create_index("uq_student_answers_submission_remediation_item", "student_answers", ["submission_id", "remediation_plan_item_id"], unique=True, postgresql_where=sa.text("remediation_plan_item_id IS NOT NULL"))

    _item_target("check_results")
    op.drop_constraint("uq_check_results_run_item", "check_results", type_="unique")
    op.create_index("uq_check_results_run_item", "check_results", ["check_run_id", "assessment_item_id"], unique=True, postgresql_where=sa.text("assessment_item_id IS NOT NULL"))
    op.create_index("uq_check_results_run_remediation_item", "check_results", ["check_run_id", "remediation_plan_item_id"], unique=True, postgresql_where=sa.text("remediation_plan_item_id IS NOT NULL"))

    _item_target("model_runs")
    op.drop_constraint("uq_model_runs_attempt", "model_runs", type_="unique")
    op.create_index("uq_model_runs_attempt", "model_runs", ["check_run_id", "assessment_item_id", "attempt_no"], unique=True, postgresql_where=sa.text("assessment_item_id IS NOT NULL"))
    op.create_index("uq_model_runs_remediation_attempt", "model_runs", ["check_run_id", "remediation_plan_item_id", "attempt_no"], unique=True, postgresql_where=sa.text("remediation_plan_item_id IS NOT NULL"))

    op.add_column("checker_events", sa.Column("remediation_plan_item_id", uuid, nullable=True))
    op.create_foreign_key("fk_checker_events_remediation_item", "checker_events", "remediation_plan_items", ["remediation_plan_item_id"], ["id"], ondelete="RESTRICT", onupdate="RESTRICT")
    op.create_check_constraint("ck_checker_events_execution_item_at_most_one", "checker_events", "num_nonnulls(assessment_item_id, remediation_plan_item_id) <= 1")


def downgrade() -> None:
    raise RuntimeError("Forward-only migration")
