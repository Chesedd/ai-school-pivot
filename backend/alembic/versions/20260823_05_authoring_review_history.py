"""Add immutable authoring review revisions and accepted revision identity.

Revision ID: 20260823_05
Revises: 20260823_04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260823_05"
down_revision = "20260823_04"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("authoring_review_revisions",
        sa.Column("session_id",sa.Uuid(),nullable=False),
        sa.Column("review_id",sa.Uuid(),nullable=False),
        sa.Column("revision_number",sa.Integer(),nullable=False),
        sa.Column("snapshot",postgresql.JSONB(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("clock_timestamp()"),nullable=False),
        sa.Column("actor_id",sa.Uuid(),nullable=False),
        sa.Column("change_summary",postgresql.JSONB(),server_default=sa.text("'{}'::jsonb"),nullable=False),
        sa.Column("id",sa.Uuid(),server_default=sa.text("gen_random_uuid()"),nullable=False),
        sa.ForeignKeyConstraint(["session_id"],["authoring_sessions.id"],name="fk_authoring_review_revisions_session",ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["review_id"],["authoring_reviews.id"],name="fk_authoring_review_revisions_review",ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id","revision_number",name="uq_authoring_review_revisions_number"),
        sa.CheckConstraint("revision_number >= 0",name="ck_authoring_review_revisions_number"))
    op.create_index("ix_authoring_review_revisions_session_number","authoring_review_revisions",["session_id","revision_number"])
    # Existing mutable rows cannot reconstruct overwritten drafts; preserve their current
    # snapshot at the matching revision number and label the limitation explicitly.
    op.execute("""INSERT INTO authoring_review_revisions
        (id, session_id, review_id, revision_number, snapshot, created_at, actor_id, change_summary)
        SELECT gen_random_uuid(), session_id, id, version - 1, draft, updated_at, owner_id,
               '{"source":"legacy_backfill","history_available":false}'::jsonb
        FROM authoring_reviews""")
    op.add_column("authoring_reviews",sa.Column("accepted_revision_id",sa.Uuid(),nullable=True))
    op.create_foreign_key("fk_authoring_reviews_accepted_revision","authoring_reviews",
        "authoring_review_revisions",["accepted_revision_id"],["id"],ondelete="RESTRICT")
    op.execute("""UPDATE authoring_reviews r SET accepted_revision_id = rr.id
        FROM authoring_review_revisions rr
        WHERE rr.review_id = r.id AND r.state = 'accepted'""")
    op.execute("""CREATE FUNCTION reject_authoring_review_revision_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'authoring review revisions are immutable';
        END $$""")
    op.execute("""CREATE TRIGGER trg_authoring_review_revisions_immutable
        BEFORE UPDATE OR DELETE ON authoring_review_revisions
        FOR EACH ROW EXECUTE FUNCTION reject_authoring_review_revision_mutation()""")
    op.drop_constraint("ck_authoring_review_audit_action","authoring_review_audit",type_="check")
    op.create_check_constraint("ck_authoring_review_audit_action","authoring_review_audit",
        "action IN ('review_started','review_changed','review_revision_created','review_revision_accepted','quality_report_created','warning_overridden','accepted','rejected')")


def downgrade():
    op.drop_constraint("ck_authoring_review_audit_action","authoring_review_audit",type_="check")
    op.execute("DELETE FROM authoring_review_audit WHERE action IN ('review_revision_created','review_revision_accepted')")
    op.create_check_constraint("ck_authoring_review_audit_action","authoring_review_audit",
        "action IN ('review_started','review_changed','quality_report_created','warning_overridden','accepted','rejected')")
    op.execute("DROP TRIGGER trg_authoring_review_revisions_immutable ON authoring_review_revisions")
    op.execute("DROP FUNCTION reject_authoring_review_revision_mutation()")
    op.drop_constraint("fk_authoring_reviews_accepted_revision","authoring_reviews",type_="foreignkey")
    op.drop_column("authoring_reviews","accepted_revision_id")
    op.drop_table("authoring_review_revisions")
