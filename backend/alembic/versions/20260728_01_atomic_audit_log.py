"""Add the append-only atomic Content Bank audit log.

Revision ID: 20260728_01
Revises: 20260727_04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260728_01"
down_revision = "20260727_04"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    action = postgresql.ENUM("task_created", "methodology_updated", "submitted_for_review",
        "returned_to_draft", "version_approved", "version_created", "task_archived",
        name="audit_action", create_type=False)
    action.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "audit_log",
        sa.Column("id", UUID, primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("task_id", UUID, nullable=False),
        sa.Column("task_version_id", UUID, nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=True),
        sa.Column("action", action, nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_audit_log_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_version_id"], ["task_versions.id"], name="fk_audit_log_task_version", ondelete="RESTRICT"),
        sa.CheckConstraint("version_no > 0", name="ck_audit_log_version_no_positive"),
    )
    op.create_index("ix_audit_log_task_occurred_at", "audit_log", ["task_id", "occurred_at"])
    op.create_index("ix_audit_log_task_action_occurred_at", "audit_log", ["task_id", "action", "occurred_at"])
    op.create_index("ix_audit_log_task_version_id", "audit_log", ["task_version_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
    postgresql.ENUM(name="audit_action", create_type=False).drop(op.get_bind(), checkfirst=False)
