"""Paper vision execution and immutable AI evidence.

Revision ID: 20261005_01
Revises: 20260923_01
"""
# ruff: noqa: E501, E702
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="20261005_01"; down_revision="20260923_01"; branch_labels=None; depends_on=None
u=postgresql.UUID(as_uuid=True); now=sa.text("clock_timestamp()")


def _guard():
    op.execute("""CREATE OR REPLACE FUNCTION checking_guard_model_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'model attempt cannot be deleted'; END IF;
      IF OLD.status<>'running' AND NEW.status=OLD.status AND OLD.check_result_id IS NULL AND NEW.check_result_id IS NOT NULL
         AND NEW IS NOT DISTINCT FROM jsonb_populate_record(OLD,jsonb_build_object('check_result_id',NEW.check_result_id))
         AND EXISTS (SELECT 1 FROM check_results r WHERE r.id=NEW.check_result_id AND r.check_run_id=OLD.check_run_id
           AND r.assessment_item_id IS NOT DISTINCT FROM OLD.assessment_item_id AND r.remediation_plan_item_id IS NOT DISTINCT FROM OLD.remediation_plan_item_id) THEN RETURN NEW; END IF;
      IF OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','invalid') OR NEW.id<>OLD.id OR NEW.check_run_id<>OLD.check_run_id
         OR NEW.assessment_item_id IS DISTINCT FROM OLD.assessment_item_id OR NEW.remediation_plan_item_id IS DISTINCT FROM OLD.remediation_plan_item_id
         OR NEW.paper_submission_id IS DISTINCT FROM OLD.paper_submission_id OR NEW.prompt_version_id<>OLD.prompt_version_id
         OR NEW.provider_id<>OLD.provider_id OR NEW.model_id<>OLD.model_id OR NEW.settings_snapshot<>OLD.settings_snapshot
         OR NEW.request_fingerprint<>OLD.request_fingerprint OR NEW.attempt_no<>OLD.attempt_no OR NEW.timeout_ms<>OLD.timeout_ms
         OR NEW.started_at<>OLD.started_at OR NEW.check_result_id IS DISTINCT FROM OLD.check_result_id THEN RAISE EXCEPTION 'model attempt identity is immutable or already terminal'; END IF;
      RETURN NEW; END $$""")


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE checking_result_status ADD VALUE IF NOT EXISTS 'not_attempted'")
        op.execute("ALTER TYPE checking_result_status ADD VALUE IF NOT EXISTS 'unreadable'")
        op.execute("ALTER TYPE checking_result_status ADD VALUE IF NOT EXISTS 'insufficient_evidence'")
        op.execute("ALTER TYPE checking_checker_type ADD VALUE IF NOT EXISTS 'paper_vision'")
    op.add_column("model_runs",sa.Column("paper_submission_id",u,nullable=True))
    op.create_foreign_key("fk_model_runs_paper_submission","model_runs","paper_submissions",["paper_submission_id"],["id"],ondelete="RESTRICT",onupdate="RESTRICT")
    op.drop_constraint("ck_model_runs_execution_item_xor","model_runs",type_="check")
    op.create_check_constraint("ck_model_runs_execution_item_xor","model_runs","num_nonnulls(assessment_item_id, remediation_plan_item_id, paper_submission_id) = 1")
    op.create_index("uq_model_runs_paper_attempt","model_runs",["check_run_id","paper_submission_id","attempt_no"],unique=True,postgresql_where=sa.text("paper_submission_id IS NOT NULL"))
    op.drop_constraint("ck_check_results_status_score","check_results",type_="check")
    op.create_check_constraint("ck_check_results_status_score","check_results","(result_status='correct' AND score_suggested=max_score) OR (result_status='incorrect' AND score_suggested=0) OR (result_status='partially_correct' AND score_suggested>0 AND score_suggested<max_score) OR (result_status IN ('unclear','insufficient_rubric','manual_required','not_attempted','unreadable','insufficient_evidence') AND score_suggested IS NULL)")
    op.create_table("paper_ai_revisions",sa.Column("id",u,primary_key=True,server_default=sa.text("gen_random_uuid()")),sa.Column("check_run_id",u,sa.ForeignKey("check_runs.id",ondelete="RESTRICT"),nullable=False),sa.Column("paper_submission_id",u,sa.ForeignKey("paper_submissions.id",ondelete="RESTRICT"),nullable=False),sa.Column("model_run_id",u,sa.ForeignKey("model_runs.id",ondelete="RESTRICT"),nullable=False),sa.Column("response_schema_version",sa.String(64),nullable=False),sa.Column("prompt_version",sa.String(64),nullable=False),sa.Column("prompt_fingerprint",sa.String(64),nullable=False),sa.Column("grading_policy_revision",sa.Integer,nullable=False),sa.Column("grading_policy_fingerprint",sa.String(64),nullable=False),sa.Column("validated_response",postgresql.JSONB,nullable=False),sa.Column("summary_draft",sa.String(4000),nullable=False),sa.Column("overall_confidence",sa.Numeric(5,4),nullable=False),sa.Column("requires_human_review",sa.Boolean,nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=now),sa.UniqueConstraint("check_run_id",name="uq_paper_ai_revisions_run"),sa.UniqueConstraint("model_run_id",name="uq_paper_ai_revisions_model"),sa.CheckConstraint("overall_confidence BETWEEN 0 AND 1 AND requires_human_review",name="ck_paper_ai_revisions_review"),sa.CheckConstraint("prompt_fingerprint ~ '^[0-9a-f]{64}$' AND grading_policy_fingerprint ~ '^[0-9a-f]{64}$'",name="ck_paper_ai_revisions_hashes"))
    op.create_table("paper_check_finding_regions",sa.Column("id",u,primary_key=True,server_default=sa.text("gen_random_uuid()")),sa.Column("check_finding_id",u,sa.ForeignKey("check_findings.id",ondelete="RESTRICT"),nullable=False),sa.Column("paper_ai_revision_id",u,sa.ForeignKey("paper_ai_revisions.id",ondelete="RESTRICT"),nullable=False),sa.Column("scan_page_id",u,sa.ForeignKey("scan_pages.id",ondelete="RESTRICT"),nullable=False),sa.Column("finding_token",sa.String(128),nullable=False),sa.Column("provider_category",sa.String(128),nullable=False),sa.Column("short_explanation",sa.String(500),nullable=False),sa.Column("detailed_explanation",sa.String(4000)),sa.Column("region",postgresql.JSONB,nullable=False),sa.Column("coordinate_space_version",sa.String(64),nullable=False),sa.Column("requires_human_review",sa.Boolean,nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=now),sa.UniqueConstraint("check_finding_id",name="uq_paper_finding_region_finding"),sa.UniqueConstraint("paper_ai_revision_id","finding_token",name="uq_paper_finding_region_token"),sa.CheckConstraint("coordinate_space_version='normalized_upright_v1'",name="ck_paper_finding_region_coordinate_space"))
    op.execute("CREATE TRIGGER trg_paper_ai_revisions_immutable BEFORE UPDATE OR DELETE ON paper_ai_revisions FOR EACH ROW EXECUTE FUNCTION checking_reject_change()")
    op.execute("CREATE TRIGGER trg_paper_check_finding_regions_immutable BEFORE UPDATE OR DELETE ON paper_check_finding_regions FOR EACH ROW EXECUTE FUNCTION checking_reject_change()")
    _guard()


def downgrade():
    op.execute("DROP TRIGGER trg_paper_check_finding_regions_immutable ON paper_check_finding_regions"); op.execute("DROP TRIGGER trg_paper_ai_revisions_immutable ON paper_ai_revisions")
    op.drop_table("paper_check_finding_regions"); op.drop_table("paper_ai_revisions")
    op.execute("ALTER TABLE check_findings DISABLE TRIGGER trg_check_findings_immutable; ALTER TABLE check_results DISABLE TRIGGER trg_check_results_immutable; ALTER TABLE checker_events DISABLE TRIGGER trg_checker_events_immutable; ALTER TABLE model_runs DISABLE TRIGGER trg_model_runs_guard; ALTER TABLE cost_events DISABLE TRIGGER trg_cost_events_immutable")
    op.execute("DELETE FROM check_findings f USING check_results r, check_runs cr WHERE f.check_result_id=r.id AND r.check_run_id=cr.id AND cr.paper_submission_id IS NOT NULL")
    op.execute("DELETE FROM checker_events e USING check_runs cr WHERE e.check_run_id=cr.id AND cr.paper_submission_id IS NOT NULL")
    op.execute("DELETE FROM check_results r USING check_runs cr WHERE r.check_run_id=cr.id AND cr.paper_submission_id IS NOT NULL")
    op.execute("DELETE FROM cost_events c USING model_runs m WHERE c.model_run_id=m.id AND m.paper_submission_id IS NOT NULL; DELETE FROM model_runs WHERE paper_submission_id IS NOT NULL")
    op.execute("ALTER TABLE check_findings ENABLE TRIGGER trg_check_findings_immutable; ALTER TABLE check_results ENABLE TRIGGER trg_check_results_immutable; ALTER TABLE checker_events ENABLE TRIGGER trg_checker_events_immutable; ALTER TABLE cost_events ENABLE TRIGGER trg_cost_events_immutable; ALTER TABLE model_runs ENABLE TRIGGER trg_model_runs_guard")
    op.drop_constraint("ck_check_results_status_score","check_results",type_="check")
    op.create_check_constraint("ck_check_results_status_score","check_results","(result_status='correct' AND score_suggested=max_score) OR (result_status='incorrect' AND score_suggested=0) OR (result_status='partially_correct' AND score_suggested>0 AND score_suggested<max_score) OR (result_status IN ('unclear','insufficient_rubric','manual_required') AND score_suggested IS NULL)")
    op.drop_index("uq_model_runs_paper_attempt",table_name="model_runs"); op.drop_constraint("ck_model_runs_execution_item_xor","model_runs",type_="check")
    op.create_check_constraint("ck_model_runs_execution_item_xor","model_runs","num_nonnulls(assessment_item_id, remediation_plan_item_id) = 1")
    op.drop_constraint("fk_model_runs_paper_submission","model_runs",type_="foreignkey"); op.drop_column("model_runs","paper_submission_id")
    # PostgreSQL cannot remove enum labels; recreate both types after all new values are gone.
    op.execute("ALTER TYPE checking_result_status RENAME TO checking_result_status_new")
    op.execute("CREATE TYPE checking_result_status AS ENUM ('correct','incorrect','partially_correct','unclear','insufficient_rubric','manual_required')")
    op.execute("ALTER TABLE check_results ALTER COLUMN result_status TYPE checking_result_status USING result_status::text::checking_result_status")
    op.execute("DROP TYPE checking_result_status_new")
    op.execute("ALTER TYPE checking_checker_type RENAME TO checking_checker_type_new")
    op.execute("CREATE TYPE checking_checker_type AS ENUM ('exact','multiple_choice','numeric','structured_expression','llm_rubric','manual_required')")
    op.execute("ALTER TABLE check_results ALTER COLUMN checker_type TYPE checking_checker_type USING checker_type::text::checking_checker_type")
    op.execute("DROP TYPE checking_checker_type_new")
    # Restore the pre-paper guard shape.
    op.execute("""CREATE OR REPLACE FUNCTION checking_guard_model_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP='DELETE' THEN RAISE EXCEPTION 'model attempt cannot be deleted'; END IF; IF OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','invalid') OR NEW.id<>OLD.id OR NEW.check_run_id<>OLD.check_run_id OR NEW.assessment_item_id IS DISTINCT FROM OLD.assessment_item_id OR NEW.remediation_plan_item_id IS DISTINCT FROM OLD.remediation_plan_item_id OR NEW.prompt_version_id<>OLD.prompt_version_id OR NEW.provider_id<>OLD.provider_id OR NEW.model_id<>OLD.model_id OR NEW.settings_snapshot<>OLD.settings_snapshot OR NEW.request_fingerprint<>OLD.request_fingerprint OR NEW.attempt_no<>OLD.attempt_no OR NEW.timeout_ms<>OLD.timeout_ms OR NEW.started_at<>OLD.started_at OR NEW.check_result_id IS DISTINCT FROM OLD.check_result_id THEN RAISE EXCEPTION 'model attempt identity is immutable or already terminal'; END IF; RETURN NEW; END $$""")
