"""Durable matching leases and append-only provider attempt lineage."""
from alembic import op

revision = "20261013_01"
down_revision = "20261012_02"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE scan_matching_runs ADD COLUMN claim_token uuid, ADD COLUMN lease_expires_at timestamptz")
    op.execute("ALTER TABLE scan_matching_runs ADD CONSTRAINT ck_matching_claim_shape CHECK ((claim_token IS NULL) = (lease_expires_at IS NULL))")
    op.execute("""CREATE TABLE scan_matching_attempts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        chunk_id uuid NOT NULL REFERENCES scan_matching_chunks(id) ON DELETE RESTRICT,
        claim_token uuid NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'running',
        started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        completed_at timestamptz,
        evidence jsonb,
        failure_code varchar(64),
        CONSTRAINT ck_matching_attempt_status CHECK (status IN ('running','succeeded','failed','interrupted')),
        CONSTRAINT ck_matching_attempt_shape CHECK (
          (status='running' AND completed_at IS NULL AND evidence IS NULL AND failure_code IS NULL) OR
          (status='succeeded' AND completed_at IS NOT NULL AND evidence IS NOT NULL AND failure_code IS NULL) OR
          (status IN ('failed','interrupted') AND completed_at IS NOT NULL AND evidence IS NULL AND failure_code IS NOT NULL))
    )""")
    op.execute("CREATE INDEX ix_matching_attempt_chunk ON scan_matching_attempts(chunk_id)")
    op.execute("""CREATE FUNCTION matching_attempt_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' OR OLD.status<>'running' OR NEW.status='running' OR
        (to_jsonb(OLD)-'status'-'completed_at'-'evidence'-'failure_code') <>
        (to_jsonb(NEW)-'status'-'completed_at'-'evidence'-'failure_code') THEN
        RAISE EXCEPTION 'matching attempt evidence is immutable'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER trg_matching_attempt_immutable BEFORE UPDATE OR DELETE ON scan_matching_attempts FOR EACH ROW EXECUTE FUNCTION matching_attempt_guard()")
    for table in ('scan_page_match_proposals','scan_page_match_candidates'):
        op.execute(f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION checking_reject_change()")


def downgrade():
    # env.py also preflights the entire requested downgrade before the first step.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM scan_matching_attempts) OR EXISTS
        (SELECT 1 FROM scan_matching_runs WHERE status='running' AND claim_token IS NOT NULL) THEN
        RAISE EXCEPTION 'matching recovery downgrade blocked: attempt/claim history requires this schema';
      END IF;
    END $$""")
    for table in ('scan_page_match_proposals','scan_page_match_candidates'):
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
    op.drop_table('scan_matching_attempts')
    op.execute('DROP FUNCTION matching_attempt_guard()')
    op.execute('ALTER TABLE scan_matching_runs DROP CONSTRAINT ck_matching_claim_shape, DROP COLUMN claim_token, DROP COLUMN lease_expires_at')
