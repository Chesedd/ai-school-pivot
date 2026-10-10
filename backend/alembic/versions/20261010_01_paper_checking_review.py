"""Teacher review revisions and deterministic paper grading.

Revision ID: 20261010_01
Revises: 20261005_01
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261010_01"
down_revision = "20261005_01"
branch_labels = None
depends_on = None
u = postgresql.UUID(as_uuid=True)
now = sa.text("clock_timestamp()")


def upgrade():
    op.create_unique_constraint(
        "uq_paper_ai_revisions_provenance",
        "paper_ai_revisions",
        ["id", "paper_submission_id", "check_run_id"],
    )
    op.create_table(
        "paper_check_reviews",
        sa.Column(
            "id", u, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "paper_submission_id",
            u,
            sa.ForeignKey("paper_submissions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "check_run_id",
            u,
            sa.ForeignKey("check_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ai_revision_id", u, nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="reviewing"),
        sa.Column("draft", postgresql.JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("latest_approved_revision_id", u),
        sa.Column(
            "created_by_user_id",
            u,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            u,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.UniqueConstraint("check_run_id", name="uq_paper_check_reviews_run"),
        sa.UniqueConstraint(
            "id",
            "paper_submission_id",
            "check_run_id",
            "ai_revision_id",
            name="uq_paper_check_reviews_provenance",
        ),
        sa.ForeignKeyConstraint(
            ["ai_revision_id", "paper_submission_id", "check_run_id"],
            [
                "paper_ai_revisions.id",
                "paper_ai_revisions.paper_submission_id",
                "paper_ai_revisions.check_run_id",
            ],
            name="fk_paper_check_reviews_ai_provenance",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "version > 0 AND state IN ('reviewing','approved')",
            name="ck_paper_check_reviews_state",
        ),
        sa.CheckConstraint(
            "state <> 'approved' OR latest_approved_revision_id IS NOT NULL",
            name="ck_paper_check_reviews_approved",
        ),
    )
    op.create_index(
        "ix_paper_check_reviews_paper", "paper_check_reviews", ["paper_submission_id"]
    )
    op.create_table(
        "paper_check_review_revisions",
        sa.Column(
            "id", u, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("review_id", u, nullable=False),
        sa.Column("paper_submission_id", u, nullable=False),
        sa.Column("check_run_id", u, nullable=False),
        sa.Column("ai_revision_id", u, nullable=False),
        sa.Column("revision_number", sa.Integer, nullable=False),
        sa.Column("revision_kind", sa.String(16), nullable=False),
        sa.Column("snapshot", postgresql.JSONB, nullable=False),
        sa.Column(
            "actor_user_id",
            u,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("grading_policy_revision", sa.Integer, nullable=False),
        sa.Column("grading_policy_fingerprint", sa.String(64), nullable=False),
        sa.Column("change_summary", postgresql.JSONB, nullable=False),
        sa.Column("calculated_total_score", sa.Numeric()),
        sa.Column("total_max_score", sa.Numeric()),
        sa.Column("calculated_percentage", sa.Numeric(7, 4)),
        sa.Column("calculated_grade", sa.String(64)),
        sa.Column("final_grade", sa.String(64)),
        sa.Column("grade_override_reason", sa.String(2000)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.UniqueConstraint(
            "review_id",
            "revision_number",
            name="uq_paper_check_review_revisions_number",
        ),
        sa.UniqueConstraint(
            "review_id", "id", name="uq_paper_check_review_revisions_identity"
        ),
        sa.ForeignKeyConstraint(
            ["review_id", "paper_submission_id", "check_run_id", "ai_revision_id"],
            [
                "paper_check_reviews.id",
                "paper_check_reviews.paper_submission_id",
                "paper_check_reviews.check_run_id",
                "paper_check_reviews.ai_revision_id",
            ],
            name="fk_paper_check_review_revisions_provenance",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "revision_number >= 0 AND revision_kind IN ('ai_seed','human_edit','approved') AND ((revision_number=0) = (revision_kind='ai_seed'))",
            name="ck_paper_check_review_revisions_kind",
        ),
        sa.CheckConstraint(
            "grading_policy_revision > 0 AND grading_policy_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_paper_check_review_revisions_policy",
        ),
        sa.CheckConstraint(
            "(revision_kind='approved' AND approved_at IS NOT NULL AND calculated_total_score IS NOT NULL AND total_max_score IS NOT NULL AND calculated_percentage IS NOT NULL AND calculated_grade IS NOT NULL AND final_grade IS NOT NULL) OR (revision_kind<>'approved' AND approved_at IS NULL AND calculated_total_score IS NULL AND total_max_score IS NULL AND calculated_percentage IS NULL AND calculated_grade IS NULL AND final_grade IS NULL AND grade_override_reason IS NULL)",
            name="ck_paper_check_review_revisions_grade",
        ),
        sa.CheckConstraint(
            "calculated_total_score >= 0 AND total_max_score > 0 AND calculated_total_score <= total_max_score AND calculated_percentage BETWEEN 0 AND 100",
            name="ck_paper_check_review_revisions_scores",
        ),
        sa.CheckConstraint(
            "(grade_override_reason IS NULL AND final_grade=calculated_grade) OR (grade_override_reason IS NOT NULL AND grade_override_reason=btrim(grade_override_reason) AND char_length(grade_override_reason) BETWEEN 1 AND 2000)",
            name="ck_paper_check_review_revisions_override",
        ),
        sa.CheckConstraint(
            "calculated_grade=btrim(calculated_grade) AND char_length(calculated_grade) BETWEEN 1 AND 64 AND final_grade=btrim(final_grade) AND char_length(final_grade) BETWEEN 1 AND 64",
            name="ck_paper_check_review_revisions_labels",
        ),
    )
    op.create_index(
        "ix_paper_check_review_revisions_history",
        "paper_check_review_revisions",
        ["review_id", "revision_number"],
    )
    op.create_foreign_key(
        "fk_paper_check_reviews_approved",
        "paper_check_reviews",
        "paper_check_review_revisions",
        ["id", "latest_approved_revision_id"],
        ["review_id", "id"],
        ondelete="RESTRICT",
    )
    op.execute(
        "CREATE TRIGGER trg_paper_check_review_revisions_immutable BEFORE UPDATE OR DELETE ON paper_check_review_revisions FOR EACH ROW EXECUTE FUNCTION checking_reject_change()"
    )
    op.execute("""CREATE FUNCTION paper_review_guard_approved_pointer() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' AND (NEW.id,NEW.paper_submission_id,NEW.check_run_id,NEW.ai_revision_id,NEW.created_by_user_id,NEW.created_at)
        IS DISTINCT FROM (OLD.id,OLD.paper_submission_id,OLD.check_run_id,OLD.ai_revision_id,OLD.created_by_user_id,OLD.created_at)
        THEN RAISE EXCEPTION 'paper review provenance is immutable'; END IF;
      IF NEW.latest_approved_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM paper_check_review_revisions r WHERE r.id=NEW.latest_approved_revision_id
        AND r.review_id=NEW.id AND r.revision_kind='approved')
        THEN RAISE EXCEPTION 'paper review pointer requires approved revision'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER trg_paper_review_approved_pointer BEFORE INSERT OR UPDATE ON paper_check_reviews FOR EACH ROW EXECUTE FUNCTION paper_review_guard_approved_pointer()"
    )


def downgrade():
    op.execute("DROP TRIGGER trg_paper_review_approved_pointer ON paper_check_reviews")
    op.execute("DROP FUNCTION paper_review_guard_approved_pointer()")
    op.drop_constraint(
        "fk_paper_check_reviews_approved", "paper_check_reviews", type_="foreignkey"
    )
    op.execute(
        "DROP TRIGGER trg_paper_check_review_revisions_immutable ON paper_check_review_revisions"
    )
    op.drop_table("paper_check_review_revisions")
    op.drop_table("paper_check_reviews")
    op.drop_constraint(
        "uq_paper_ai_revisions_provenance", "paper_ai_revisions", type_="unique"
    )
