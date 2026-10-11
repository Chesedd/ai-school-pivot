"""Manual provenance on the canonical paper review lineage.

Downgrade refuses manual data before any DDL; no historical evidence is deleted.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261011_03"
down_revision = "20261011_02"
branch_labels = None
depends_on = None

SOURCE = "(source_kind='ai' AND check_run_id IS NOT NULL AND ai_revision_id IS NOT NULL) OR (source_kind='manual' AND check_run_id IS NULL AND ai_revision_id IS NULL)"
OLD_KIND = "revision_number >= 0 AND revision_kind IN ('ai_seed','human_edit','approved') AND ((revision_number=0) = (revision_kind='ai_seed'))"
NEW_KIND = "revision_number >= 0 AND revision_kind IN ('ai_seed','manual_seed','human_edit','approved') AND ((revision_number=0) = (revision_kind IN ('ai_seed','manual_seed'))) AND (revision_kind <> 'ai_seed' OR source_kind='ai') AND (revision_kind <> 'manual_seed' OR source_kind='manual')"
OLD_XOR = "(source_submission_id IS NOT NULL AND source_paper_submission_id IS NULL AND source_paper_publication_id IS NULL) OR (source_submission_id IS NULL AND source_paper_submission_id IS NOT NULL AND source_paper_publication_id IS NOT NULL)"
EVENTS = "'batch.created','batch.cancelled','artifact.attached','artifact.extraction_status_changed','page.created','page.status_changed','grouping.draft_created','grouping.updated','grouping.confirmed','paper_submission.created','batch.checking_started','batch.checking_completed','batch.checking_reopened','batch.completed'"


def guard(manual):
    fields = [
        "id",
        "paper_submission_id",
        "check_run_id",
        "ai_revision_id",
        "created_by_user_id",
        "created_at",
    ]
    if manual:
        fields.append("source_kind")
    new = ",".join("NEW." + x for x in fields)
    old = ",".join("OLD." + x for x in fields)
    op.execute(f"""CREATE OR REPLACE FUNCTION paper_review_guard_approved_pointer() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' AND ({new}) IS DISTINCT FROM ({old})
        THEN RAISE EXCEPTION 'paper review provenance is immutable'; END IF;
      IF NEW.latest_approved_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM paper_check_review_revisions r WHERE r.id=NEW.latest_approved_revision_id
        AND r.review_id=NEW.id AND r.revision_kind='approved')
        THEN RAISE EXCEPTION 'paper review pointer requires approved revision'; END IF;
      RETURN NEW;
    END $$""")


def events(manual):
    op.drop_constraint(
        "ck_scan_checking_events_type", "scan_checking_events", type_="check"
    )
    op.create_check_constraint(
        "ck_scan_checking_events_type",
        "scan_checking_events",
        f"event_type IN ({EVENTS}"
        + (",'paper.manual_review_started')" if manual else ")"),
    )


def upgrade():
    for table in ("paper_check_reviews", "paper_check_review_revisions"):
        op.add_column(
            table,
            sa.Column(
                "source_kind", sa.String(16), nullable=False, server_default="ai"
            ),
        )
        for column in ("check_run_id", "ai_revision_id"):
            op.alter_column(table, column, nullable=True)
        op.create_check_constraint(f"ck_{table}_source", table, SOURCE)
    op.create_unique_constraint(
        "uq_paper_check_reviews_source",
        "paper_check_reviews",
        ["id", "paper_submission_id", "source_kind"],
    )
    op.create_foreign_key(
        "fk_paper_check_review_revisions_source",
        "paper_check_review_revisions",
        "paper_check_reviews",
        ["review_id", "paper_submission_id", "source_kind"],
        ["id", "paper_submission_id", "source_kind"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_paper_check_reviews_manual",
        "paper_check_reviews",
        ["paper_submission_id"],
        unique=True,
        postgresql_where=sa.text("source_kind='manual'"),
    )
    op.drop_constraint(
        "ck_paper_check_review_revisions_kind",
        "paper_check_review_revisions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_paper_check_review_revisions_kind", "paper_check_review_revisions", NEW_KIND
    )
    op.alter_column("remediation_plans", "source_check_run_id", nullable=True)
    op.drop_constraint(
        "ck_remediation_plans_source_xor", "remediation_plans", type_="check"
    )
    op.create_check_constraint(
        "ck_remediation_plans_source_xor",
        "remediation_plans",
        OLD_XOR.replace(
            "source_submission_id IS NOT NULL AND",
            "source_submission_id IS NOT NULL AND source_check_run_id IS NOT NULL AND",
        ),
    )
    guard(True)
    events(True)


def downgrade():
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM paper_check_reviews WHERE source_kind='manual') OR EXISTS (SELECT 1 FROM remediation_plans WHERE source_check_run_id IS NULL) THEN RAISE EXCEPTION 'Cannot downgrade while manual paper reviews or remediation plans exist'; END IF; END $$"
    )
    guard(False)
    events(False)
    op.drop_constraint(
        "ck_remediation_plans_source_xor", "remediation_plans", type_="check"
    )
    op.create_check_constraint(
        "ck_remediation_plans_source_xor", "remediation_plans", OLD_XOR
    )
    op.alter_column("remediation_plans", "source_check_run_id", nullable=False)
    op.drop_constraint(
        "ck_paper_check_review_revisions_kind",
        "paper_check_review_revisions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_paper_check_review_revisions_kind", "paper_check_review_revisions", OLD_KIND
    )
    op.drop_index("uq_paper_check_reviews_manual", table_name="paper_check_reviews")
    op.drop_constraint(
        "fk_paper_check_review_revisions_source",
        "paper_check_review_revisions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_paper_check_reviews_source", "paper_check_reviews", type_="unique"
    )
    for table in ("paper_check_review_revisions", "paper_check_reviews"):
        op.drop_constraint(f"ck_{table}_source", table, type_="check")
        for column in ("check_run_id", "ai_revision_id"):
            op.alter_column(table, column, nullable=False)
        op.drop_column(table, "source_kind")
