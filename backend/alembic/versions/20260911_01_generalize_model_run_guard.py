"""Generalize the model-run guard for remediation execution targets.

Revision ID: 20260911_01
Revises: 20260907_04
"""
from alembic import op


revision = "20260911_01"
down_revision = "20260907_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE OR REPLACE FUNCTION checking_guard_model_run() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'model attempt cannot be deleted';
        END IF;

        IF OLD.status <> 'running'
           AND NEW.status = OLD.status
           AND OLD.check_result_id IS NULL
           AND NEW.check_result_id IS NOT NULL
           AND NEW IS NOT DISTINCT FROM jsonb_populate_record(
             OLD, jsonb_build_object('check_result_id', NEW.check_result_id)
           )
           AND EXISTS (
             SELECT 1 FROM check_results r
             WHERE r.id = NEW.check_result_id
               AND r.check_run_id = OLD.check_run_id
               AND r.assessment_item_id IS NOT DISTINCT FROM OLD.assessment_item_id
               AND r.remediation_plan_item_id IS NOT DISTINCT FROM OLD.remediation_plan_item_id
           )
        THEN
          RETURN NEW;
        END IF;

        IF OLD.status <> 'running'
           OR NEW.status NOT IN ('succeeded', 'failed', 'invalid')
           OR NEW.id <> OLD.id
           OR NEW.check_run_id <> OLD.check_run_id
           OR NEW.assessment_item_id IS DISTINCT FROM OLD.assessment_item_id
           OR NEW.remediation_plan_item_id IS DISTINCT FROM OLD.remediation_plan_item_id
           OR NEW.prompt_version_id <> OLD.prompt_version_id
           OR NEW.provider_id <> OLD.provider_id
           OR NEW.model_id <> OLD.model_id
           OR NEW.settings_snapshot <> OLD.settings_snapshot
           OR NEW.request_fingerprint <> OLD.request_fingerprint
           OR NEW.attempt_no <> OLD.attempt_no
           OR NEW.timeout_ms <> OLD.timeout_ms
           OR NEW.started_at <> OLD.started_at
           OR NEW.check_result_id IS DISTINCT FROM OLD.check_result_id
        THEN
          RAISE EXCEPTION 'model attempt identity is immutable or already terminal';
        END IF;
        RETURN NEW;
      END $$
    """)


def downgrade() -> None:
    raise RuntimeError("Forward-only migration")
