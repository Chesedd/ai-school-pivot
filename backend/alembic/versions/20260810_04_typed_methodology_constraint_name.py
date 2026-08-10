"""Align the scoring-policy unique constraint name with ORM metadata.

Revision ID: 20260810_04
Revises: 20260810_03
"""
from alembic import op

revision = "20260810_04"
down_revision = "20260810_03"
branch_labels = depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE choice_scoring_policies "
        "RENAME CONSTRAINT choice_scoring_policies_task_version_id_key "
        "TO uq_choice_scoring_policy_version"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE choice_scoring_policies "
        "RENAME CONSTRAINT uq_choice_scoring_policy_version "
        "TO choice_scoring_policies_task_version_id_key"
    )
