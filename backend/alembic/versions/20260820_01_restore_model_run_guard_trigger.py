"""Restore the Phase 4.9 model-run immutability trigger.

Revision ID: 20260820_01
Revises: 20260819_01
"""
from alembic import op

revision = "20260820_01"
down_revision = "20260819_01"
branch_labels = depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TRIGGER trg_model_runs_guard "
        "BEFORE UPDATE OR DELETE ON model_runs "
        "FOR EACH ROW EXECUTE FUNCTION checking_guard_model_run()"
    )


def downgrade() -> None:
    # Keep the guard installed: revision 20260819_01 already requires this
    # invariant, and its downgrade removes it before restoring the old guard.
    pass
