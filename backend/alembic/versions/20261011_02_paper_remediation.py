"""Generalize remediation provenance; refuse lossy paper downgrades."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261011_02"
down_revision = "20261011_01"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("remediation_plans", "source_submission_id", nullable=True)
    for column, target in (
        ("source_paper_submission_id", "paper_submissions"),
        ("source_paper_publication_id", "paper_result_publications"),
    ):
        op.add_column(
            "remediation_plans",
            sa.Column(column, postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            f"fk_remediation_plans_{column}",
            "remediation_plans",
            target,
            [column],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_check_constraint(
        "ck_remediation_plans_source_xor",
        "remediation_plans",
        "(source_submission_id IS NOT NULL AND source_paper_submission_id IS NULL AND source_paper_publication_id IS NULL) OR (source_submission_id IS NULL AND source_paper_submission_id IS NOT NULL AND source_paper_publication_id IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_remediation_plan_publication",
        "remediation_plans",
        ["id", "source_paper_publication_id"],
    )
    op.create_foreign_key(
        "fk_remediation_plan_paper_publication",
        "remediation_plans",
        "paper_result_publications",
        ["source_paper_submission_id", "source_paper_publication_id"],
        ["paper_submission_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_remediation_plans_paper_publication",
        "remediation_plans",
        ["source_paper_submission_id", "source_paper_publication_id"],
    )
    for kind, field in (
        ("item", "assessment_item_id"),
        ("finding", "publication_finding_id"),
    ):
        columns = [
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "remediation_plan_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column(
                "source_paper_publication_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column(field, postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("clock_timestamp()"),
            ),
            sa.UniqueConstraint(
                "remediation_plan_id", field, name=f"uq_remediation_paper_{kind}_signal"
            ),
            sa.ForeignKeyConstraint(
                ["remediation_plan_id", "source_paper_publication_id"],
                [
                    "remediation_plans.id",
                    "remediation_plans.source_paper_publication_id",
                ],
                ondelete="CASCADE",
                name=f"fk_remediation_paper_{kind}_plan_publication",
            ),
        ]
        if kind == "item":
            columns.append(
                sa.ForeignKeyConstraint(
                    [field], ["assessment_items.id"], ondelete="RESTRICT"
                )
            )
        op.create_table(f"remediation_plan_paper_{kind}_signals", *columns)


def downgrade():
    # Check before any DDL. Preserve historical teacher intent and digital data.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM remediation_plans WHERE source_paper_submission_id IS NOT NULL) THEN RAISE EXCEPTION 'Cannot downgrade while paper remediation plans exist'; END IF; END $$"
    )
    for kind in ("finding", "item"):
        op.drop_table(f"remediation_plan_paper_{kind}_signals")
    op.drop_index(
        "ix_remediation_plans_paper_publication", table_name="remediation_plans"
    )
    op.drop_constraint(
        "fk_remediation_plan_paper_publication", "remediation_plans", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_remediation_plan_publication", "remediation_plans", type_="unique"
    )
    op.drop_constraint(
        "ck_remediation_plans_source_xor", "remediation_plans", type_="check"
    )
    for column in ("source_paper_publication_id", "source_paper_submission_id"):
        op.drop_constraint(
            f"fk_remediation_plans_{column}", "remediation_plans", type_="foreignkey"
        )
        op.drop_column("remediation_plans", column)
    op.alter_column("remediation_plans", "source_submission_id", nullable=False)
