"""Add Phase 4.1 Checking persistence foundation.

Revision ID: 20260810_01
Revises: 20260808_02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260810_01"
down_revision = "20260808_02"
branch_labels = depends_on = None

ENUMS = (
    (
        "checking_run_status",
        (
            "pending",
            "running",
            "completed",
            "completed_with_review_required",
            "failed_retryable",
            "failed_terminal",
        ),
    ),
    (
        "checking_result_status",
        (
            "correct",
            "incorrect",
            "partially_correct",
            "insufficient_rubric",
            "manual_required",
        ),
    ),
    (
        "checking_checker_type",
        (
            "exact",
            "multiple_choice",
            "numeric",
            "structured_expression",
            "llm_rubric",
            "manual_required",
        ),
    ),
    ("checking_finding_type", ("rubric", "typical_error", "skill", "general")),
    ("checking_finding_severity", ("info", "minor", "major", "critical")),
    (
        "checking_event_type",
        ("run_created", "run_transition", "result_recorded", "model_attempt"),
    ),
    ("checking_model_status", ("running", "succeeded", "failed", "invalid")),
)
metadata = sa.MetaData()
for name in ("student_submissions", "assessment_items", "task_versions"):
    sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))


def enum(name: str) -> postgresql.ENUM:
    values = dict(ENUMS)[name]
    return postgresql.ENUM(*values, name=name, create_type=False)


def id_column() -> sa.Column:
    return sa.Column(
        "id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


check_runs = sa.Table(
    "check_runs",
    metadata,
    sa.Column("submission_id", sa.Uuid(), nullable=False),
    sa.Column("request_key", sa.String(128), nullable=False),
    sa.Column("request_hash", sa.String(64), nullable=False),
    sa.Column("handoff_version", sa.SmallInteger(), nullable=False),
    sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
    sa.Column("input_fingerprint", sa.String(64), nullable=False),
    sa.Column("snapshot_schema_version", sa.String(64), nullable=False),
    sa.Column("routing_version", sa.String(64), nullable=False),
    sa.Column("checker_set_version", sa.String(64), nullable=False),
    sa.Column("threshold_policy_version", sa.String(64), nullable=False),
    sa.Column("prompt_model_policy_version", sa.String(64), nullable=False),
    sa.Column(
        "status",
        enum("checking_run_status"),
        nullable=False,
        server_default=sa.text("'pending'::checking_run_status"),
    ),
    sa.Column("attempt_no", sa.Integer(), nullable=False),
    sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column(
        "requested_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    ),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
    sa.Column("failure_code", sa.String(64)),
    sa.Column("failure_detail", sa.Text()),
    sa.Column("supersedes_run_id", sa.Uuid()),
    sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    id_column(),
    sa.ForeignKeyConstraint(
        ["submission_id"],
        ["student_submissions.id"],
        name="fk_check_runs_submission",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["supersedes_run_id"],
        ["check_runs.id"],
        name="fk_check_runs_supersedes",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.UniqueConstraint(
        "submission_id", "request_key", name="uq_check_runs_submission_request"
    ),
    sa.UniqueConstraint(
        "submission_id", "attempt_no", name="uq_check_runs_submission_attempt"
    ),
    sa.CheckConstraint(
        "request_key = btrim(request_key) AND char_length(request_key) BETWEEN 1 AND 128",
        name="ck_check_runs_request_key",
    ),
    sa.CheckConstraint(
        "request_hash ~ '^[0-9a-f]{64}$'", name="ck_check_runs_request_hash"
    ),
    sa.CheckConstraint(
        "input_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_check_runs_fingerprint"
    ),
    sa.CheckConstraint(
        "handoff_version > 0 AND attempt_no > 0 AND retry_count >= 0 AND row_version > 0",
        name="ck_check_runs_positive_values",
    ),
    sa.CheckConstraint(
        "snapshot_schema_version = btrim(snapshot_schema_version) AND char_length(snapshot_schema_version) BETWEEN 1 AND 64 AND routing_version = btrim(routing_version) AND char_length(routing_version) BETWEEN 1 AND 64 AND checker_set_version = btrim(checker_set_version) AND char_length(checker_set_version) BETWEEN 1 AND 64 AND threshold_policy_version = btrim(threshold_policy_version) AND char_length(threshold_policy_version) BETWEEN 1 AND 64 AND prompt_model_policy_version = btrim(prompt_model_policy_version) AND char_length(prompt_model_policy_version) BETWEEN 1 AND 64",
        name="ck_check_runs_versions",
    ),
    sa.CheckConstraint(
        "supersedes_run_id IS NULL OR supersedes_run_id <> id",
        name="ck_check_runs_not_self_supersede",
    ),
    sa.CheckConstraint(
        "(status='pending' AND started_at IS NULL AND finished_at IS NULL AND failure_code IS NULL) OR (status='running' AND started_at IS NOT NULL AND finished_at IS NULL AND failure_code IS NULL) OR (status IN ('completed','completed_with_review_required') AND started_at IS NOT NULL AND finished_at IS NOT NULL AND failure_code IS NULL) OR (status='failed_retryable' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND failure_code IS NOT NULL) OR (status='failed_terminal' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND failure_code IS NOT NULL)",
        name="ck_check_runs_status_timestamps",
    ),
    sa.CheckConstraint(
        "finished_at IS NULL OR finished_at >= started_at",
        name="ck_check_runs_finished_order",
    ),
    sa.CheckConstraint(
        "heartbeat_at IS NULL OR (started_at IS NOT NULL AND heartbeat_at >= started_at)",
        name="ck_check_runs_heartbeat_order",
    ),
    sa.CheckConstraint(
        "failure_code IS NULL OR (failure_code=btrim(failure_code) AND char_length(failure_code) BETWEEN 1 AND 64)",
        name="ck_check_runs_failure_code",
    ),
    sa.CheckConstraint(
        "failure_detail IS NULL OR char_length(failure_detail)<=2000",
        name="ck_check_runs_failure_detail",
    ),
)
sa.Index(
    "uq_check_runs_one_active",
    check_runs.c.submission_id,
    unique=True,
    postgresql_where=sa.text("status IN ('pending','running')"),
)
sa.Index(
    "ix_check_runs_history",
    check_runs.c.submission_id,
    sa.text("requested_at DESC"),
    sa.text("id DESC"),
)
sa.Index("ix_check_runs_active", check_runs.c.submission_id, check_runs.c.status)
sa.Index(
    "ix_check_runs_worker",
    check_runs.c.status,
    check_runs.c.requested_at,
    check_runs.c.id,
)
sa.Index("ix_check_runs_supersedes", check_runs.c.supersedes_run_id)

check_results = sa.Table(
    "check_results",
    metadata,
    sa.Column("check_run_id", sa.Uuid(), nullable=False),
    sa.Column("assessment_item_id", sa.Uuid(), nullable=False),
    sa.Column("task_version_id", sa.Uuid(), nullable=False),
    sa.Column("checker_type", enum("checking_checker_type"), nullable=False),
    sa.Column("checker_version", sa.String(64), nullable=False),
    sa.Column("schema_version", sa.String(64), nullable=False),
    sa.Column("result_status", enum("checking_result_status"), nullable=False),
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
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    ),
    id_column(),
    sa.ForeignKeyConstraint(
        ["check_run_id"],
        ["check_runs.id"],
        name="fk_check_results_run",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["assessment_item_id"],
        ["assessment_items.id"],
        name="fk_check_results_item",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["task_version_id"],
        ["task_versions.id"],
        name="fk_check_results_version",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.UniqueConstraint(
        "check_run_id", "assessment_item_id", name="uq_check_results_run_item"
    ),
    sa.CheckConstraint(
        "max_score>0 AND (score_suggested IS NULL OR score_suggested BETWEEN 0 AND max_score)",
        name="ck_check_results_score_range",
    ),
    sa.CheckConstraint(
        "confidence BETWEEN 0 AND 1", name="ck_check_results_confidence"
    ),
    sa.CheckConstraint(
        "(result_status='correct' AND score_suggested=max_score) OR (result_status='incorrect' AND score_suggested=0) OR (result_status='partially_correct' AND score_suggested>0 AND score_suggested<max_score) OR (result_status IN ('insufficient_rubric','manual_required') AND score_suggested IS NULL)",
        name="ck_check_results_status_score",
    ),
    sa.CheckConstraint(
        "(needs_human_review AND review_reason IS NOT NULL AND char_length(btrim(review_reason)) BETWEEN 1 AND 500) OR (NOT needs_human_review AND review_reason IS NULL)",
        name="ck_check_results_review",
    ),
    sa.CheckConstraint(
        "char_length(summary) BETWEEN 1 AND 1000 AND char_length(checker_version) BETWEEN 1 AND 64 AND char_length(schema_version) BETWEEN 1 AND 64",
        name="ck_check_results_text",
    ),
)
sa.Index(
    "ix_check_results_item_created",
    check_results.c.assessment_item_id,
    sa.text("created_at DESC"),
)
sa.Index("ix_check_results_task_version", check_results.c.task_version_id)
sa.Index(
    "ix_check_results_review",
    check_results.c.needs_human_review,
    check_results.c.created_at,
)

check_findings = sa.Table(
    "check_findings",
    metadata,
    sa.Column("check_result_id", sa.Uuid(), nullable=False),
    sa.Column("finding_type", enum("checking_finding_type"), nullable=False),
    sa.Column("rubric_item_id", sa.Uuid()),
    sa.Column("typical_error_id", sa.Uuid()),
    sa.Column("skill_id", sa.Uuid()),
    sa.Column("snapshot_code", sa.String(128)),
    sa.Column("snapshot_title", sa.String(500)),
    sa.Column("snapshot_criterion", sa.String(2000)),
    sa.Column("severity", enum("checking_finding_severity"), nullable=False),
    sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
    sa.Column("evidence", postgresql.JSONB(), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    ),
    id_column(),
    sa.ForeignKeyConstraint(
        ["check_result_id"],
        ["check_results.id"],
        name="fk_check_findings_result",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.CheckConstraint(
        "confidence BETWEEN 0 AND 1", name="ck_check_findings_confidence"
    ),
    sa.CheckConstraint(
        "rubric_item_id IS NOT NULL OR typical_error_id IS NOT NULL OR skill_id IS NOT NULL OR finding_type='general'",
        name="ck_check_findings_source",
    ),
    sa.CheckConstraint(
        "snapshot_code IS NULL OR char_length(snapshot_code)<=128",
        name="ck_check_findings_code",
    ),
    sa.CheckConstraint(
        "snapshot_title IS NULL OR char_length(snapshot_title)<=500",
        name="ck_check_findings_title",
    ),
    sa.CheckConstraint(
        "snapshot_criterion IS NULL OR char_length(snapshot_criterion)<=2000",
        name="ck_check_findings_criterion",
    ),
)
sa.Index("ix_check_findings_result", check_findings.c.check_result_id)
sa.Index(
    "ix_check_findings_provenance",
    check_findings.c.rubric_item_id,
    check_findings.c.typical_error_id,
    check_findings.c.skill_id,
)

checker_events = sa.Table(
    "checker_events",
    metadata,
    sa.Column("check_run_id", sa.Uuid(), nullable=False),
    sa.Column("check_result_id", sa.Uuid()),
    sa.Column("assessment_item_id", sa.Uuid()),
    sa.Column("event_type", enum("checking_event_type"), nullable=False),
    sa.Column("from_status", enum("checking_run_status")),
    sa.Column("to_status", enum("checking_run_status")),
    sa.Column("reason_code", sa.String(64)),
    sa.Column(
        "details",
        postgresql.JSONB(),
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    ),
    sa.Column(
        "occurred_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    ),
    id_column(),
    sa.ForeignKeyConstraint(
        ["check_run_id"],
        ["check_runs.id"],
        name="fk_checker_events_run",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["check_result_id"],
        ["check_results.id"],
        name="fk_checker_events_result",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["assessment_item_id"],
        ["assessment_items.id"],
        name="fk_checker_events_item",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.CheckConstraint(
        "(event_type='run_transition' AND from_status IS NOT NULL AND to_status IS NOT NULL AND from_status<>to_status) OR (event_type<>'run_transition' AND from_status IS NULL AND to_status IS NULL)",
        name="ck_checker_events_transition",
    ),
    sa.CheckConstraint(
        "reason_code IS NULL OR char_length(reason_code) BETWEEN 1 AND 64",
        name="ck_checker_events_reason",
    ),
)
sa.Index(
    "ix_checker_events_run_time",
    checker_events.c.check_run_id,
    checker_events.c.occurred_at,
    checker_events.c.id,
)
sa.Index(
    "ix_checker_events_type_time",
    checker_events.c.event_type,
    checker_events.c.occurred_at,
)

prompt_versions = sa.Table(
    "prompt_versions",
    metadata,
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("semantic_version", sa.String(64), nullable=False),
    sa.Column("template_hash", sa.String(64), nullable=False),
    sa.Column("output_schema_version", sa.String(64), nullable=False),
    sa.Column("template_text", sa.Text(), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    ),
    sa.Column("retired_at", sa.DateTime(timezone=True)),
    id_column(),
    sa.UniqueConstraint(
        "name", "semantic_version", "template_hash", name="uq_prompt_versions_identity"
    ),
    sa.CheckConstraint(
        "name=btrim(name) AND char_length(name) BETWEEN 1 AND 120 AND semantic_version=btrim(semantic_version) AND char_length(semantic_version) BETWEEN 1 AND 64 AND output_schema_version=btrim(output_schema_version) AND char_length(output_schema_version) BETWEEN 1 AND 64",
        name="ck_prompt_versions_names",
    ),
    sa.CheckConstraint(
        "template_hash ~ '^[0-9a-f]{64}$'", name="ck_prompt_versions_hash"
    ),
    sa.CheckConstraint(
        "retired_at IS NULL OR retired_at>=created_at",
        name="ck_prompt_versions_retirement",
    ),
)

model_runs = sa.Table(
    "model_runs",
    metadata,
    sa.Column("check_run_id", sa.Uuid(), nullable=False),
    sa.Column("assessment_item_id", sa.Uuid(), nullable=False),
    sa.Column("prompt_version_id", sa.Uuid(), nullable=False),
    sa.Column("check_result_id", sa.Uuid()),
    sa.Column("provider_id", sa.String(128), nullable=False),
    sa.Column("model_id", sa.String(128), nullable=False),
    sa.Column("settings_snapshot", postgresql.JSONB(), nullable=False),
    sa.Column("request_fingerprint", sa.String(64), nullable=False),
    sa.Column("provider_request_id", sa.String(256)),
    sa.Column(
        "status",
        enum("checking_model_status"),
        nullable=False,
        server_default=sa.text("'running'::checking_model_status"),
    ),
    sa.Column("error_code", sa.String(64)),
    sa.Column("error_detail", sa.String(2000)),
    sa.Column("attempt_no", sa.Integer(), nullable=False),
    sa.Column("timeout_ms", sa.Integer(), nullable=False),
    sa.Column("latency_ms", sa.Integer()),
    sa.Column("raw_output", sa.Text()),
    sa.Column("validated_output", postgresql.JSONB()),
    sa.Column("validation_errors", postgresql.JSONB()),
    sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column(
        "started_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    ),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    id_column(),
    sa.ForeignKeyConstraint(
        ["check_run_id"],
        ["check_runs.id"],
        name="fk_model_runs_run",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["assessment_item_id"],
        ["assessment_items.id"],
        name="fk_model_runs_item",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["prompt_version_id"],
        ["prompt_versions.id"],
        name="fk_model_runs_prompt",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["check_result_id"],
        ["check_results.id"],
        name="fk_model_runs_result",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.UniqueConstraint(
        "check_run_id", "assessment_item_id", "attempt_no", name="uq_model_runs_attempt"
    ),
    sa.CheckConstraint(
        "attempt_no>0 AND timeout_ms>=0 AND (latency_ms IS NULL OR latency_ms>=0) AND input_tokens>=0 AND output_tokens>=0 AND cached_tokens>=0",
        name="ck_model_runs_numbers",
    ),
    sa.CheckConstraint(
        "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_model_runs_fingerprint"
    ),
    sa.CheckConstraint(
        "provider_id=btrim(provider_id) AND char_length(provider_id) BETWEEN 1 AND 128 AND model_id=btrim(model_id) AND char_length(model_id) BETWEEN 1 AND 128",
        name="ck_model_runs_ids",
    ),
    sa.CheckConstraint(
        "(status='running' AND finished_at IS NULL AND error_code IS NULL) OR (status='succeeded' AND finished_at IS NOT NULL AND validated_output IS NOT NULL AND error_code IS NULL) OR (status IN ('failed','invalid') AND finished_at IS NOT NULL AND error_code IS NOT NULL)",
        name="ck_model_runs_status",
    ),
    sa.CheckConstraint(
        "finished_at IS NULL OR finished_at>=started_at", name="ck_model_runs_time"
    ),
)
sa.Index("ix_model_runs_status", model_runs.c.status, model_runs.c.started_at)
sa.Index("ix_model_runs_provider_request", model_runs.c.provider_request_id)

cost_events = sa.Table(
    "cost_events",
    metadata,
    sa.Column("model_run_id", sa.Uuid(), nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("input_tokens", sa.Integer(), nullable=False),
    sa.Column("output_tokens", sa.Integer(), nullable=False),
    sa.Column("cached_tokens", sa.Integer(), nullable=False),
    sa.Column("amount", sa.Numeric(18, 8), nullable=False),
    sa.Column("pricing_version", sa.String(64), nullable=False),
    sa.Column("pricing_source", sa.String(128), nullable=False),
    sa.Column(
        "occurred_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    ),
    id_column(),
    sa.ForeignKeyConstraint(
        ["model_run_id"],
        ["model_runs.id"],
        name="fk_cost_events_model_run",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.UniqueConstraint(
        "model_run_id", "pricing_version", name="uq_cost_events_model_pricing"
    ),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_cost_events_currency"),
    sa.CheckConstraint(
        "input_tokens>=0 AND output_tokens>=0 AND cached_tokens>=0 AND amount>=0",
        name="ck_cost_events_values",
    ),
    sa.CheckConstraint(
        "pricing_version=btrim(pricing_version) AND char_length(pricing_version) BETWEEN 1 AND 64 AND pricing_source=btrim(pricing_source) AND char_length(pricing_source) BETWEEN 1 AND 128",
        name="ck_cost_events_pricing",
    ),
)

TABLES = (
    check_runs,
    check_results,
    check_findings,
    checker_events,
    prompt_versions,
    model_runs,
    cost_events,
)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS:
        postgresql.ENUM(*values, name=name).create(bind)
    for table in TABLES:
        table.create(bind)
    op.execute(
        """CREATE FUNCTION checking_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'checking history is immutable'; END $$"""
    )
    for table in ("check_results", "check_findings", "checker_events", "cost_events"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION checking_reject_change()"
        )
    op.execute("""CREATE FUNCTION checking_guard_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'check run history cannot be deleted'; END IF;
      IF NEW.id<>OLD.id OR NEW.submission_id<>OLD.submission_id OR NEW.request_key<>OLD.request_key OR NEW.request_hash<>OLD.request_hash OR NEW.handoff_version<>OLD.handoff_version OR NEW.input_snapshot<>OLD.input_snapshot OR NEW.input_fingerprint<>OLD.input_fingerprint OR NEW.snapshot_schema_version<>OLD.snapshot_schema_version OR NEW.routing_version<>OLD.routing_version OR NEW.checker_set_version<>OLD.checker_set_version OR NEW.threshold_policy_version<>OLD.threshold_policy_version OR NEW.prompt_model_policy_version<>OLD.prompt_model_policy_version OR NEW.attempt_no<>OLD.attempt_no OR NEW.requested_at<>OLD.requested_at OR NEW.supersedes_run_id IS DISTINCT FROM OLD.supersedes_run_id THEN RAISE EXCEPTION 'check run identity/snapshot is immutable'; END IF;
      IF NEW.row_version<>OLD.row_version+1 THEN RAISE EXCEPTION 'check run mutation requires CAS version increment'; END IF;
      IF NEW.status<>OLD.status AND NOT ((OLD.status='pending' AND NEW.status IN ('running','failed_terminal')) OR (OLD.status='running' AND NEW.status IN ('completed','completed_with_review_required','failed_retryable','failed_terminal')) OR (OLD.status='failed_retryable' AND NEW.status='pending')) THEN RAISE EXCEPTION 'invalid check run transition'; END IF;
      RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER trg_check_runs_guard BEFORE UPDATE OR DELETE ON check_runs FOR EACH ROW EXECUTE FUNCTION checking_guard_run()"
    )
    op.execute("""CREATE FUNCTION checking_guard_prompt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'prompt version cannot be deleted'; END IF;
      IF NEW.id<>OLD.id OR NEW.name<>OLD.name OR NEW.semantic_version<>OLD.semantic_version OR NEW.template_hash<>OLD.template_hash OR NEW.output_schema_version<>OLD.output_schema_version OR NEW.template_text<>OLD.template_text OR NEW.created_at<>OLD.created_at OR OLD.retired_at IS NOT NULL OR NEW.retired_at IS NULL THEN RAISE EXCEPTION 'prompt content is immutable and retirement is one-way'; END IF; RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER trg_prompt_versions_guard BEFORE UPDATE OR DELETE ON prompt_versions FOR EACH ROW EXECUTE FUNCTION checking_guard_prompt()"
    )
    op.execute("""CREATE FUNCTION checking_guard_model_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'model attempt cannot be deleted'; END IF;
      IF OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','invalid') OR NEW.id<>OLD.id OR NEW.check_run_id<>OLD.check_run_id OR NEW.assessment_item_id<>OLD.assessment_item_id OR NEW.prompt_version_id<>OLD.prompt_version_id OR NEW.provider_id<>OLD.provider_id OR NEW.model_id<>OLD.model_id OR NEW.settings_snapshot<>OLD.settings_snapshot OR NEW.request_fingerprint<>OLD.request_fingerprint OR NEW.attempt_no<>OLD.attempt_no OR NEW.timeout_ms<>OLD.timeout_ms OR NEW.started_at<>OLD.started_at THEN RAISE EXCEPTION 'model attempt identity is immutable or already terminal'; END IF; RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER trg_model_runs_guard BEFORE UPDATE OR DELETE ON model_runs FOR EACH ROW EXECUTE FUNCTION checking_guard_model_run()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP TRIGGER trg_model_runs_guard ON model_runs")
    op.execute("DROP FUNCTION checking_guard_model_run()")
    op.execute("DROP TRIGGER trg_prompt_versions_guard ON prompt_versions")
    op.execute("DROP FUNCTION checking_guard_prompt()")
    op.execute("DROP TRIGGER trg_check_runs_guard ON check_runs")
    op.execute("DROP FUNCTION checking_guard_run()")
    for table in ("cost_events", "checker_events", "check_findings", "check_results"):
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
    op.execute("DROP FUNCTION checking_reject_change()")
    for table in reversed(TABLES):
        table.drop(bind)
    for name, _ in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(bind)
