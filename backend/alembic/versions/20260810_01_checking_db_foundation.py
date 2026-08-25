"""Add Phase 4.1 Checking persistence foundation.

Revision ID: 20260810_01
Revises: 20260808_02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.infrastructure.checking_models import (CheckRun, CheckFinding,
    CheckerEvent, PromptVersion, ModelRun, CostEvent)

revision = "20260810_01"
down_revision = "20260808_02"
branch_labels = depends_on = None

ENUMS = (
    ("checking_run_status", ("pending","running","completed","completed_with_review_required","failed_retryable","failed_terminal")),
    ("checking_result_status", ("correct","incorrect","partially_correct","insufficient_rubric","manual_required")),
    ("checking_checker_type", ("exact","multiple_choice","numeric","structured_expression","llm_rubric","manual_required")),
    ("checking_finding_type", ("rubric","typical_error","skill","general")),
    ("checking_finding_severity", ("info","minor","major","critical")),
    ("checking_event_type", ("run_created","run_transition","result_recorded","model_attempt")),
    ("checking_model_status", ("running","succeeded","failed","invalid")),
)
def _check_result_table() -> sa.Table:
    """Return the check-results schema owned by this revision.

    Do not use the ORM table here: ``20260819_01`` owns the later observability
    columns, so importing live metadata makes this historical revision drift.
    """
    metadata = sa.MetaData()
    # Local stubs let SQLAlchemy resolve the foreign keys while only this table
    # is created by the revision (the referenced tables already exist).
    for name in ("check_runs", "assessment_items", "task_versions"):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    result_status = postgresql.ENUM(
        "correct", "incorrect", "partially_correct", "insufficient_rubric",
        "manual_required", name="checking_result_status", create_type=False,
    )
    checker_type = postgresql.ENUM(
        "exact", "multiple_choice", "numeric", "structured_expression",
        "llm_rubric", "manual_required", name="checking_checker_type",
        create_type=False,
    )
    table = sa.Table(
        "check_results", metadata,
        sa.Column("check_run_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_item_id", sa.Uuid(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("checker_type", checker_type, nullable=False),
        sa.Column("checker_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("result_status", result_status, nullable=False),
        sa.Column("score_suggested", sa.Numeric(10, 2)),
        sa.Column("max_score", sa.Numeric(10, 2), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("summary", sa.String(1000), nullable=False),
        sa.Column("student_feedback_draft", sa.String(4000)),
        sa.Column("teacher_summary", sa.String(2000)),
        sa.Column("needs_human_review", sa.Boolean(), nullable=False),
        sa.Column("review_reason", sa.String(500)),
        sa.Column("model_limitations", sa.String(2000)),
        sa.Column("validated_result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("clock_timestamp()")),
        sa.Column("id", sa.Uuid(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.ForeignKeyConstraint(["check_run_id"], ["check_runs.id"], name="fk_check_results_run", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["assessment_item_id"], ["assessment_items.id"], name="fk_check_results_item", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["task_version_id"], ["task_versions.id"], name="fk_check_results_version", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.UniqueConstraint("check_run_id", "assessment_item_id", name="uq_check_results_run_item"),
        sa.CheckConstraint("max_score>0 AND (score_suggested IS NULL OR score_suggested BETWEEN 0 AND max_score)", name="ck_check_results_score_range"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_check_results_confidence"),
        sa.CheckConstraint("(result_status='correct' AND score_suggested=max_score) OR (result_status='incorrect' AND score_suggested=0) OR (result_status='partially_correct' AND score_suggested>0 AND score_suggested<max_score) OR (result_status IN ('insufficient_rubric','manual_required') AND score_suggested IS NULL)", name="ck_check_results_status_score"),
        sa.CheckConstraint("(needs_human_review AND review_reason IS NOT NULL AND char_length(btrim(review_reason)) BETWEEN 1 AND 500) OR (NOT needs_human_review AND review_reason IS NULL)", name="ck_check_results_review"),
        sa.CheckConstraint("char_length(summary) BETWEEN 1 AND 1000 AND char_length(checker_version) BETWEEN 1 AND 64 AND char_length(schema_version) BETWEEN 1 AND 64", name="ck_check_results_text"),
    )
    sa.Index("ix_check_results_item_created", table.c.assessment_item_id, sa.text("created_at DESC"))
    sa.Index("ix_check_results_task_version", table.c.task_version_id)
    sa.Index("ix_check_results_review", table.c.needs_human_review, table.c.created_at)
    return table


TABLES = (CheckRun.__table__, _check_result_table(), CheckFinding.__table__, CheckerEvent.__table__, PromptVersion.__table__, ModelRun.__table__, CostEvent.__table__)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS: postgresql.ENUM(*values, name=name).create(bind)
    for table in TABLES: table.create(bind)
    op.execute("""CREATE FUNCTION checking_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'checking history is immutable'; END $$""")
    for table in ("check_results", "check_findings", "checker_events", "cost_events"):
        op.execute(f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION checking_reject_change()")
    op.execute("""CREATE FUNCTION checking_guard_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'check run history cannot be deleted'; END IF;
      IF NEW.id<>OLD.id OR NEW.submission_id<>OLD.submission_id OR NEW.request_key<>OLD.request_key OR NEW.request_hash<>OLD.request_hash OR NEW.handoff_version<>OLD.handoff_version OR NEW.input_snapshot<>OLD.input_snapshot OR NEW.input_fingerprint<>OLD.input_fingerprint OR NEW.snapshot_schema_version<>OLD.snapshot_schema_version OR NEW.routing_version<>OLD.routing_version OR NEW.checker_set_version<>OLD.checker_set_version OR NEW.threshold_policy_version<>OLD.threshold_policy_version OR NEW.prompt_model_policy_version<>OLD.prompt_model_policy_version OR NEW.attempt_no<>OLD.attempt_no OR NEW.requested_at<>OLD.requested_at OR NEW.supersedes_run_id IS DISTINCT FROM OLD.supersedes_run_id THEN RAISE EXCEPTION 'check run identity/snapshot is immutable'; END IF;
      IF NEW.row_version<>OLD.row_version+1 THEN RAISE EXCEPTION 'check run mutation requires CAS version increment'; END IF;
      IF NEW.status<>OLD.status AND NOT ((OLD.status='pending' AND NEW.status IN ('running','failed_terminal')) OR (OLD.status='running' AND NEW.status IN ('completed','completed_with_review_required','failed_retryable','failed_terminal')) OR (OLD.status='failed_retryable' AND NEW.status='pending')) THEN RAISE EXCEPTION 'invalid check run transition'; END IF;
      RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER trg_check_runs_guard BEFORE UPDATE OR DELETE ON check_runs FOR EACH ROW EXECUTE FUNCTION checking_guard_run()")
    op.execute("""CREATE FUNCTION checking_guard_prompt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'prompt version cannot be deleted'; END IF;
      IF NEW.id<>OLD.id OR NEW.name<>OLD.name OR NEW.semantic_version<>OLD.semantic_version OR NEW.template_hash<>OLD.template_hash OR NEW.output_schema_version<>OLD.output_schema_version OR NEW.template_text<>OLD.template_text OR NEW.created_at<>OLD.created_at OR OLD.retired_at IS NOT NULL OR NEW.retired_at IS NULL THEN RAISE EXCEPTION 'prompt content is immutable and retirement is one-way'; END IF; RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER trg_prompt_versions_guard BEFORE UPDATE OR DELETE ON prompt_versions FOR EACH ROW EXECUTE FUNCTION checking_guard_prompt()")
    op.execute("""CREATE FUNCTION checking_guard_model_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'model attempt cannot be deleted'; END IF;
      IF OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','invalid') OR NEW.id<>OLD.id OR NEW.check_run_id<>OLD.check_run_id OR NEW.assessment_item_id<>OLD.assessment_item_id OR NEW.prompt_version_id<>OLD.prompt_version_id OR NEW.provider_id<>OLD.provider_id OR NEW.model_id<>OLD.model_id OR NEW.settings_snapshot<>OLD.settings_snapshot OR NEW.request_fingerprint<>OLD.request_fingerprint OR NEW.attempt_no<>OLD.attempt_no OR NEW.timeout_ms<>OLD.timeout_ms OR NEW.started_at<>OLD.started_at THEN RAISE EXCEPTION 'model attempt identity is immutable or already terminal'; END IF; RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER trg_model_runs_guard BEFORE UPDATE OR DELETE ON model_runs FOR EACH ROW EXECUTE FUNCTION checking_guard_model_run()")


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP TRIGGER trg_model_runs_guard ON model_runs"); op.execute("DROP FUNCTION checking_guard_model_run()")
    op.execute("DROP TRIGGER trg_prompt_versions_guard ON prompt_versions"); op.execute("DROP FUNCTION checking_guard_prompt()")
    op.execute("DROP TRIGGER trg_check_runs_guard ON check_runs"); op.execute("DROP FUNCTION checking_guard_run()")
    for table in ("cost_events", "checker_events", "check_findings", "check_results"):
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
    op.execute("DROP FUNCTION checking_reject_change()")
    for table in reversed(TABLES): table.drop(bind)
    for name, _ in reversed(ENUMS): postgresql.ENUM(name=name).drop(bind)
