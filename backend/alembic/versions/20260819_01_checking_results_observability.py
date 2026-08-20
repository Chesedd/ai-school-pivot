"""Checking results, confidence and observability. Revision ID: 20260819_01."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="20260819_01"; down_revision="20260810_04"; branch_labels=depends_on=None
OLD=("correct","incorrect","partially_correct","insufficient_rubric","manual_required")
NEW=("correct","incorrect","partially_correct","unclear","insufficient_rubric","manual_required")

def _enum(values):
    op.execute("ALTER TYPE checking_result_status RENAME TO checking_result_status_old")
    postgresql.ENUM(*values,name="checking_result_status").create(op.get_bind())
    op.execute("ALTER TABLE check_results ALTER COLUMN result_status TYPE checking_result_status USING result_status::text::checking_result_status")
    op.execute("DROP TYPE checking_result_status_old")

def _model_guard():
    op.execute("""CREATE FUNCTION checking_guard_model_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'model attempt cannot be deleted'; END IF;
      IF OLD.status<>'running' AND NEW.status=OLD.status AND OLD.check_result_id IS NULL AND NEW.check_result_id IS NOT NULL
         AND NEW IS NOT DISTINCT FROM jsonb_populate_record(OLD, jsonb_build_object('check_result_id',NEW.check_result_id))
         AND EXISTS (SELECT 1 FROM check_results r WHERE r.id=NEW.check_result_id AND r.check_run_id=OLD.check_run_id AND r.assessment_item_id=OLD.assessment_item_id)
      THEN RETURN NEW; END IF;
      IF OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','invalid') OR NEW.id<>OLD.id OR NEW.check_run_id<>OLD.check_run_id OR NEW.assessment_item_id<>OLD.assessment_item_id OR NEW.prompt_version_id<>OLD.prompt_version_id OR NEW.provider_id<>OLD.provider_id OR NEW.model_id<>OLD.model_id OR NEW.settings_snapshot<>OLD.settings_snapshot OR NEW.request_fingerprint<>OLD.request_fingerprint OR NEW.attempt_no<>OLD.attempt_no OR NEW.timeout_ms<>OLD.timeout_ms OR NEW.started_at<>OLD.started_at OR NEW.check_result_id IS DISTINCT FROM OLD.check_result_id THEN RAISE EXCEPTION 'model attempt identity is immutable or already terminal'; END IF; RETURN NEW; END $$""")

def upgrade():
    op.drop_constraint("ck_check_results_status_score","check_results",type_="check"); _enum(NEW)
    op.create_check_constraint("ck_check_results_status_score","check_results","(result_status='correct' AND score_suggested=max_score) OR (result_status='incorrect' AND score_suggested=0) OR (result_status='partially_correct' AND score_suggested>0 AND score_suggested<max_score) OR (result_status IN ('unclear','insufficient_rubric','manual_required') AND score_suggested IS NULL)")
    op.add_column("check_results",sa.Column("reason_code",sa.String(64)))
    op.add_column("check_results",sa.Column("confidence_policy_version",sa.String(64)))
    op.add_column("check_results",sa.Column("confidence_details",postgresql.JSONB()))
    op.execute(
        "ALTER TABLE check_results "
        "DISABLE TRIGGER trg_check_results_immutable"
    )
    op.execute("""UPDATE check_results r SET reason_code='legacy_result', confidence_policy_version=x.threshold_policy_version,
      confidence_details=jsonb_build_object('schema_version','checking_confidence_gate_v1','base',to_char(r.confidence,'FM0.0000'),'effective',to_char(r.confidence,'FM0.0000'),'policy_version',x.threshold_policy_version,'reasons',jsonb_build_array('legacy_result'),'penalties','[]'::jsonb,'total_penalty','0.0000','needs_human_review',r.needs_human_review,'review_reason',r.review_reason) FROM check_runs x WHERE x.id=r.check_run_id""")
    op.execute(
        "ALTER TABLE check_results "
        "ENABLE TRIGGER trg_check_results_immutable"
    )
    for c in ("reason_code","confidence_policy_version","confidence_details"): op.alter_column("check_results",c,nullable=False)
    op.create_check_constraint("ck_check_results_reason_code","check_results","reason_code=btrim(reason_code) AND char_length(reason_code) BETWEEN 1 AND 64")
    op.create_check_constraint("ck_check_results_confidence_policy","check_results","confidence_policy_version=btrim(confidence_policy_version) AND char_length(confidence_policy_version) BETWEEN 1 AND 64")
    op.create_check_constraint("ck_check_results_confidence_details","check_results","jsonb_typeof(confidence_details)='object' AND confidence_details->>'effective'=to_char(confidence,'FM0.0000')")
    op.execute("DROP TRIGGER trg_model_runs_guard ON model_runs"); op.execute("DROP FUNCTION checking_guard_model_run()"); _model_guard()

def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM check_results WHERE result_status='unclear') THEN RAISE EXCEPTION 'cannot downgrade: unclear checking results exist'; END IF; END $$""")
    op.execute("DROP TRIGGER trg_model_runs_guard ON model_runs"); op.execute("DROP FUNCTION checking_guard_model_run()")
    # Restore the original guard exactly.
    op.execute("""CREATE FUNCTION checking_guard_model_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP='DELETE' THEN RAISE EXCEPTION 'model attempt cannot be deleted'; END IF; IF OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','invalid') OR NEW.id<>OLD.id OR NEW.check_run_id<>OLD.check_run_id OR NEW.assessment_item_id<>OLD.assessment_item_id OR NEW.prompt_version_id<>OLD.prompt_version_id OR NEW.provider_id<>OLD.provider_id OR NEW.model_id<>OLD.model_id OR NEW.settings_snapshot<>OLD.settings_snapshot OR NEW.request_fingerprint<>OLD.request_fingerprint OR NEW.attempt_no<>OLD.attempt_no OR NEW.timeout_ms<>OLD.timeout_ms OR NEW.started_at<>OLD.started_at THEN RAISE EXCEPTION 'model attempt identity is immutable or already terminal'; END IF; RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER trg_model_runs_guard BEFORE UPDATE OR DELETE ON model_runs FOR EACH ROW EXECUTE FUNCTION checking_guard_model_run()")
    for c in ("ck_check_results_confidence_details","ck_check_results_confidence_policy","ck_check_results_reason_code"): op.drop_constraint(c,"check_results",type_="check")
    for c in ("confidence_details","confidence_policy_version","reason_code"): op.drop_column("check_results",c)
    op.drop_constraint("ck_check_results_status_score","check_results",type_="check"); _enum(OLD)
    op.create_check_constraint("ck_check_results_status_score","check_results","(result_status='correct' AND score_suggested=max_score) OR (result_status='incorrect' AND score_suggested=0) OR (result_status='partially_correct' AND score_suggested>0 AND score_suggested<max_score) OR (result_status IN ('insufficient_rubric','manual_required') AND score_suggested IS NULL)")
