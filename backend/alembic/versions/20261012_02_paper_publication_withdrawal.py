"""Immutable publication withdrawal and nullable active visibility.

Downgrade never restores visibility. It refuses states the old guard cannot
represent; otherwise it drops withdrawal audit records (explicit schema loss).
"""

from alembic import op

revision = "20261012_02"
down_revision = "20261012_01"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE paper_result_publication_withdrawals (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      publication_id uuid NOT NULL REFERENCES paper_result_publications(id) ON DELETE RESTRICT,
      withdrawn_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      reason varchar(500) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CONSTRAINT uq_paper_publication_withdrawal UNIQUE(publication_id),
      CONSTRAINT ck_paper_withdrawal_reason CHECK
        (length(btrim(reason, ' ' || chr(9) || chr(10) || chr(11) || chr(12) || chr(13)))>0 AND length(reason)<=500 AND reason=btrim(reason, ' ' || chr(9) || chr(10) || chr(11) || chr(12) || chr(13)))
    )""")
    op.execute(
        "CREATE INDEX ix_paper_withdrawal_created ON paper_result_publication_withdrawals(created_at,publication_id)"
    )
    op.execute(
        "CREATE TRIGGER trg_paper_withdrawal_immutable BEFORE UPDATE OR DELETE ON paper_result_publication_withdrawals FOR EACH ROW EXECUTE FUNCTION checking_reject_change()"
    )
    op.execute(
        "ALTER TABLE paper_result_publication_states ALTER COLUMN active_publication_id DROP NOT NULL"
    )
    op.execute("""CREATE OR REPLACE FUNCTION paper_publication_guard_state() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'publication state cannot be deleted'; END IF;
      IF TG_OP='UPDATE' AND NEW.paper_submission_id<>OLD.paper_submission_id THEN
        RAISE EXCEPTION 'publication pointer identity conflict'; END IF;
      IF NEW.active_publication_id IS NULL THEN
        IF TG_OP<>'UPDATE' OR OLD.active_publication_id IS NULL OR NEW.version<>OLD.version
          OR NOT EXISTS (SELECT 1 FROM paper_result_publication_withdrawals w
                         WHERE w.publication_id=OLD.active_publication_id) THEN
          RAISE EXCEPTION 'publication withdrawal provenance mismatch'; END IF;
        RETURN NEW;
      END IF;
      IF TG_OP='UPDATE' AND NEW.version<>OLD.version+1 THEN
        RAISE EXCEPTION 'publication pointer version conflict'; END IF;
      IF EXISTS (SELECT 1 FROM paper_result_publication_withdrawals w
                 WHERE w.publication_id=NEW.active_publication_id) THEN
        RAISE EXCEPTION 'withdrawn publication cannot be active'; END IF;
      IF NOT EXISTS (SELECT 1 FROM paper_result_publications p
        WHERE p.id=NEW.active_publication_id AND p.paper_submission_id=NEW.paper_submission_id
          AND p.publication_number=NEW.version AND
          ((TG_OP='INSERT' AND p.publication_number=1 AND p.supersedes_publication_id IS NULL)
           OR (TG_OP='UPDATE' AND EXISTS (SELECT 1 FROM paper_result_publications prior
             WHERE prior.id=p.supersedes_publication_id
               AND prior.paper_submission_id=OLD.paper_submission_id
               AND prior.publication_number=OLD.version)))) THEN
        RAISE EXCEPTION 'publication pointer provenance mismatch'; END IF;
      RETURN NEW;
    END $$""")
    # Lock the same paper identity even for direct SQL callers. Deferred check
    # allows row insertion followed by pointer clearing within one transaction.
    op.execute("""CREATE FUNCTION paper_withdrawal_guard_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE paper_id uuid;
    BEGIN
      SELECT paper_submission_id INTO paper_id FROM paper_result_publications WHERE id=NEW.publication_id;
      PERFORM id FROM paper_submissions WHERE id=paper_id FOR UPDATE;
      IF NOT EXISTS (SELECT 1 FROM paper_result_publication_states
                     WHERE paper_submission_id=paper_id AND active_publication_id=NEW.publication_id) THEN
        RAISE EXCEPTION 'publication is not active'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER trg_paper_withdrawal_active BEFORE INSERT ON paper_result_publication_withdrawals FOR EACH ROW EXECUTE FUNCTION paper_withdrawal_guard_insert()"
    )
    op.execute("""CREATE FUNCTION paper_withdrawal_guard_committed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS (SELECT 1 FROM paper_result_publication_states WHERE active_publication_id=NEW.publication_id) THEN
        RAISE EXCEPTION 'withdrawn publication cannot remain active'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_paper_withdrawal_committed AFTER INSERT ON paper_result_publication_withdrawals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION paper_withdrawal_guard_committed()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM paper_result_publication_states WHERE active_publication_id IS NULL) THEN
        RAISE EXCEPTION 'PR15 downgrade blocked: withdrawn visibility cannot be represented; no publication is resurrected';
      END IF;
    END $$""")
    op.drop_table("paper_result_publication_withdrawals")
    op.execute("DROP FUNCTION paper_withdrawal_guard_insert()")
    op.execute("DROP FUNCTION paper_withdrawal_guard_committed()")
    op.execute(
        "ALTER TABLE paper_result_publication_states ALTER COLUMN active_publication_id SET NOT NULL"
    )
    op.execute("""CREATE OR REPLACE FUNCTION paper_publication_guard_state() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'publication cannot be revoked'; END IF;
      IF TG_OP='UPDATE' AND (NEW.paper_submission_id<>OLD.paper_submission_id OR NEW.version<>OLD.version+1)
        THEN RAISE EXCEPTION 'publication pointer version conflict'; END IF;
      IF NOT EXISTS (SELECT 1 FROM paper_result_publications p WHERE p.id=NEW.active_publication_id
        AND p.paper_submission_id=NEW.paper_submission_id AND p.publication_number=NEW.version
        AND ((TG_OP='INSERT' AND p.publication_number=1 AND p.supersedes_publication_id IS NULL)
          OR (TG_OP='UPDATE' AND p.supersedes_publication_id=OLD.active_publication_id)))
        THEN RAISE EXCEPTION 'publication pointer provenance mismatch'; END IF;
      RETURN NEW;
    END $$""")
