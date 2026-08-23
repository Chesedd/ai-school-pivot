"""Allow deterministic authoring quality audit events.

Revision ID: 20260823_04
Revises: 20260823_03
"""
from alembic import op

revision = "20260823_04"
down_revision = "20260823_03"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_authoring_review_audit_action", "authoring_review_audit", type_="check")
    op.create_check_constraint("ck_authoring_review_audit_action", "authoring_review_audit",
        "action IN ('review_started','review_changed','quality_report_created','warning_overridden','accepted','rejected')")


def downgrade():
    op.execute("DELETE FROM authoring_review_audit WHERE action IN ('quality_report_created','warning_overridden')")
    op.drop_constraint("ck_authoring_review_audit_action", "authoring_review_audit", type_="check")
    op.create_check_constraint("ck_authoring_review_audit_action", "authoring_review_audit",
        "action IN ('review_started','review_changed','accepted','rejected')")
