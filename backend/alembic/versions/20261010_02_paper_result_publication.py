"""Explicit immutable paper publication and active pointer.

Revision ID: 20261010_02
Revises: 20261010_01
"""

from alembic import op

revision = "20261010_02"
down_revision = "20261010_01"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE paper_result_publications (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      paper_submission_id uuid NOT NULL REFERENCES paper_submissions(id) ON DELETE RESTRICT,
      review_id uuid NOT NULL,
      approved_review_revision_id uuid NOT NULL,
      published_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      publication_number integer NOT NULL,
      supersedes_publication_id uuid,
      publication_snapshot jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CONSTRAINT uq_paper_publication_number UNIQUE(paper_submission_id,publication_number),
      CONSTRAINT uq_paper_publication_identity UNIQUE(paper_submission_id,id),
      CONSTRAINT fk_paper_publication_revision FOREIGN KEY(review_id,approved_review_revision_id)
        REFERENCES paper_check_review_revisions(review_id,id) ON DELETE RESTRICT,
      CONSTRAINT fk_paper_publication_supersedes FOREIGN KEY(paper_submission_id,supersedes_publication_id)
        REFERENCES paper_result_publications(paper_submission_id,id) ON DELETE RESTRICT,
      CONSTRAINT ck_paper_publication_number CHECK(publication_number>0 AND (supersedes_publication_id IS NULL OR supersedes_publication_id<>id)),
      CONSTRAINT ck_paper_publication_snapshot CHECK(jsonb_typeof(publication_snapshot)='object'
        AND publication_snapshot->>'schema_version' IS NOT NULL
        AND publication_snapshot->>'schema_version'='paper_result_publication.v1')
    )""")
    op.execute("""CREATE TABLE paper_result_publication_states (
      paper_submission_id uuid PRIMARY KEY REFERENCES paper_submissions(id) ON DELETE RESTRICT,
      active_publication_id uuid NOT NULL,
      version integer NOT NULL,
      updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CONSTRAINT fk_paper_publication_active FOREIGN KEY(paper_submission_id,active_publication_id)
        REFERENCES paper_result_publications(paper_submission_id,id) ON DELETE RESTRICT,
      CONSTRAINT ck_paper_publication_state_version CHECK(version>0)
    )""")
    op.execute(
        "CREATE INDEX ix_paper_publication_revision ON paper_result_publications(approved_review_revision_id)"
    )
    op.execute("""CREATE FUNCTION paper_publication_guard_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM paper_check_review_revisions r
        WHERE r.id=NEW.approved_review_revision_id AND r.review_id=NEW.review_id
        AND r.paper_submission_id=NEW.paper_submission_id AND r.revision_kind='approved'
        AND NEW.publication_snapshot->>'paper_submission_id'=r.paper_submission_id::text
        AND NEW.publication_snapshot->>'approved_review_revision_id'=r.id::text)
        THEN RAISE EXCEPTION 'publication requires matching approved revision'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER trg_paper_publication_revision BEFORE INSERT ON paper_result_publications FOR EACH ROW EXECUTE FUNCTION paper_publication_guard_revision()"
    )
    op.execute(
        "CREATE TRIGGER trg_paper_publication_immutable BEFORE UPDATE OR DELETE ON paper_result_publications FOR EACH ROW EXECUTE FUNCTION checking_reject_change()"
    )
    op.execute("""CREATE FUNCTION paper_publication_guard_state() RETURNS trigger LANGUAGE plpgsql AS $$
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
    op.execute(
        "CREATE TRIGGER trg_paper_publication_state BEFORE INSERT OR UPDATE OR DELETE ON paper_result_publication_states FOR EACH ROW EXECUTE FUNCTION paper_publication_guard_state()"
    )


def downgrade():
    op.drop_table("paper_result_publication_states")
    op.execute("DROP FUNCTION paper_publication_guard_state()")
    op.drop_table("paper_result_publications")
    op.execute("DROP FUNCTION paper_publication_guard_revision()")
