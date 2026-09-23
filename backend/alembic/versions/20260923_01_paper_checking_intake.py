"""Add frozen paper grading policies and paper CheckRun targets.

Revision ID: 20260923_01
Revises: 20260915_02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260923_01"
down_revision = "20260915_02"
branch_labels = None
depends_on = None
u = postgresql.UUID(as_uuid=True)
now = sa.text("clock_timestamp()")


def upgrade():
    op.create_table(
        "scan_grading_policy_revisions",
        sa.Column("id", u, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("batch_id", u, sa.ForeignKey("assessment_scan_batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("assessment_variant_id", u, sa.ForeignKey("assessment_variants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("policy_schema_version", sa.String(64), nullable=False),
        sa.Column("compiler_version", sa.String(64), nullable=False),
        sa.Column("prompt_policy_version", sa.String(64), nullable=False),
        sa.Column("policy_json", postgresql.JSONB, nullable=False),
        sa.Column("policy_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", u, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("supersedes_policy_id", u, sa.ForeignKey("scan_grading_policy_revisions.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("batch_id", "assessment_variant_id", "revision", name="uq_scan_grading_policy_revision"),
        sa.CheckConstraint("revision > 0", name="ck_scan_grading_policy_revision_positive"),
        sa.CheckConstraint("policy_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_scan_grading_policy_fingerprint"),
        sa.CheckConstraint("supersedes_policy_id IS NULL OR supersedes_policy_id <> id", name="ck_scan_grading_policy_not_self"),
        sa.CheckConstraint("char_length(btrim(policy_schema_version)) BETWEEN 1 AND 64 AND char_length(btrim(compiler_version)) BETWEEN 1 AND 64 AND char_length(btrim(prompt_policy_version)) BETWEEN 1 AND 64", name="ck_scan_grading_policy_versions"),
    )
    op.create_index("ix_scan_grading_policy_current", "scan_grading_policy_revisions", ["batch_id", "assessment_variant_id", sa.text("revision DESC")])
    op.execute("CREATE TRIGGER trg_scan_grading_policy_revisions_immutable BEFORE UPDATE OR DELETE ON scan_grading_policy_revisions FOR EACH ROW EXECUTE FUNCTION checking_reject_change()")
    op.add_column("check_runs", sa.Column("paper_submission_id", u, nullable=True))
    op.create_foreign_key("fk_check_runs_paper_submission", "check_runs", "paper_submissions", ["paper_submission_id"], ["id"], ondelete="RESTRICT", onupdate="RESTRICT")
    op.alter_column("check_runs", "submission_id", existing_type=u, nullable=True)
    op.create_check_constraint("ck_check_runs_execution_source_xor", "check_runs", "num_nonnulls(submission_id, paper_submission_id) = 1")
    op.create_unique_constraint("uq_check_runs_paper_request", "check_runs", ["paper_submission_id", "request_key"])
    op.create_unique_constraint("uq_check_runs_paper_attempt", "check_runs", ["paper_submission_id", "attempt_no"])
    op.create_index("uq_check_runs_one_active_paper", "check_runs", ["paper_submission_id"], unique=True, postgresql_where=sa.text("paper_submission_id IS NOT NULL AND status IN ('pending','running')"))
    op.create_index("ix_check_runs_paper_history", "check_runs", ["paper_submission_id", sa.text("requested_at DESC"), sa.text("id DESC")])
    op.create_index("ix_check_runs_paper_active", "check_runs", ["paper_submission_id", "status"])
    op.execute("""CREATE OR REPLACE FUNCTION checking_guard_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'check run history cannot be deleted'; END IF;
      IF NEW.id<>OLD.id OR NEW.submission_id IS DISTINCT FROM OLD.submission_id OR NEW.paper_submission_id IS DISTINCT FROM OLD.paper_submission_id OR NEW.request_key<>OLD.request_key OR NEW.request_hash<>OLD.request_hash OR NEW.handoff_version<>OLD.handoff_version OR NEW.input_snapshot<>OLD.input_snapshot OR NEW.input_fingerprint<>OLD.input_fingerprint OR NEW.snapshot_schema_version<>OLD.snapshot_schema_version OR NEW.routing_version<>OLD.routing_version OR NEW.checker_set_version<>OLD.checker_set_version OR NEW.threshold_policy_version<>OLD.threshold_policy_version OR NEW.prompt_model_policy_version<>OLD.prompt_model_policy_version OR NEW.attempt_no<>OLD.attempt_no OR NEW.requested_at<>OLD.requested_at OR NEW.supersedes_run_id IS DISTINCT FROM OLD.supersedes_run_id THEN RAISE EXCEPTION 'check run identity/snapshot is immutable'; END IF;
      IF NEW.row_version<>OLD.row_version+1 THEN RAISE EXCEPTION 'check run mutation requires CAS version increment'; END IF;
      IF NEW.status<>OLD.status AND NOT ((OLD.status='pending' AND NEW.status IN ('running','failed_terminal')) OR (OLD.status='running' AND NEW.status IN ('completed','completed_with_review_required','failed_retryable','failed_terminal')) OR (OLD.status='failed_retryable' AND NEW.status='pending')) THEN RAISE EXCEPTION 'invalid check run transition'; END IF;
      RETURN NEW; END $$""")


def downgrade():
    op.execute("""CREATE OR REPLACE FUNCTION checking_guard_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'check run history cannot be deleted'; END IF;
      IF NEW.id<>OLD.id OR NEW.submission_id<>OLD.submission_id OR NEW.request_key<>OLD.request_key OR NEW.request_hash<>OLD.request_hash OR NEW.handoff_version<>OLD.handoff_version OR NEW.input_snapshot<>OLD.input_snapshot OR NEW.input_fingerprint<>OLD.input_fingerprint OR NEW.snapshot_schema_version<>OLD.snapshot_schema_version OR NEW.routing_version<>OLD.routing_version OR NEW.checker_set_version<>OLD.checker_set_version OR NEW.threshold_policy_version<>OLD.threshold_policy_version OR NEW.prompt_model_policy_version<>OLD.prompt_model_policy_version OR NEW.attempt_no<>OLD.attempt_no OR NEW.requested_at<>OLD.requested_at OR NEW.supersedes_run_id IS DISTINCT FROM OLD.supersedes_run_id THEN RAISE EXCEPTION 'check run identity/snapshot is immutable'; END IF;
      IF NEW.row_version<>OLD.row_version+1 THEN RAISE EXCEPTION 'check run mutation requires CAS version increment'; END IF;
      IF NEW.status<>OLD.status AND NOT ((OLD.status='pending' AND NEW.status IN ('running','failed_terminal')) OR (OLD.status='running' AND NEW.status IN ('completed','completed_with_review_required','failed_retryable','failed_terminal')) OR (OLD.status='failed_retryable' AND NEW.status='pending')) THEN RAISE EXCEPTION 'invalid check run transition'; END IF;
      RETURN NEW; END $$""")
    op.drop_index("ix_check_runs_paper_active", table_name="check_runs")
    op.drop_index("ix_check_runs_paper_history", table_name="check_runs")
    op.drop_index("uq_check_runs_one_active_paper", table_name="check_runs")
    op.drop_constraint("uq_check_runs_paper_attempt", "check_runs", type_="unique")
    op.drop_constraint("uq_check_runs_paper_request", "check_runs", type_="unique")
    op.drop_constraint("ck_check_runs_execution_source_xor", "check_runs", type_="check")
    op.alter_column("check_runs", "submission_id", existing_type=u, nullable=False)
    op.drop_constraint("fk_check_runs_paper_submission", "check_runs", type_="foreignkey")
    op.drop_column("check_runs", "paper_submission_id")
    op.execute("DROP TRIGGER trg_scan_grading_policy_revisions_immutable ON scan_grading_policy_revisions")
    op.drop_index("ix_scan_grading_policy_current", table_name="scan_grading_policy_revisions")
    op.drop_table("scan_grading_policy_revisions")
