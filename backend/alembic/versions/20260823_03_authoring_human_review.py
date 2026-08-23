"""Add bounded mutable authoring review state and its audit stream.

Revision ID: 20260823_03
Revises: 20260823_02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="20260823_03"; down_revision="20260823_02"; branch_labels=None; depends_on=None


def upgrade():
    op.create_table("authoring_reviews",
        sa.Column("session_id",sa.Uuid(),nullable=False),sa.Column("owner_id",sa.Uuid(),nullable=False),
        sa.Column("state",sa.String(16),server_default="reviewing",nullable=False),
        sa.Column("draft",postgresql.JSONB(),nullable=False),sa.Column("version",sa.Integer(),server_default="1",nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("clock_timestamp()"),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.text("clock_timestamp()"),nullable=False),
        sa.Column("id",sa.Uuid(),server_default=sa.text("gen_random_uuid()"),nullable=False),
        sa.ForeignKeyConstraint(["session_id"],["authoring_sessions.id"],name="fk_authoring_reviews_session",ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),sa.UniqueConstraint("session_id",name="uq_authoring_reviews_session"),
        sa.CheckConstraint("state IN ('reviewing','accepted','rejected')",name="ck_authoring_reviews_state"),
        sa.CheckConstraint("version > 0",name="ck_authoring_reviews_version"))
    op.create_index("ix_authoring_reviews_session_state","authoring_reviews",["session_id","state"])
    op.create_table("authoring_review_audit",
        sa.Column("session_id",sa.Uuid(),nullable=False),sa.Column("review_id",sa.Uuid(),nullable=False),
        sa.Column("actor_id",sa.Uuid(),nullable=False),sa.Column("action",sa.String(32),nullable=False),
        sa.Column("review_version",sa.Integer(),nullable=False),
        sa.Column("details",postgresql.JSONB(),server_default=sa.text("'{}'::jsonb"),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("clock_timestamp()"),nullable=False),
        sa.Column("id",sa.Uuid(),server_default=sa.text("gen_random_uuid()"),nullable=False),
        sa.ForeignKeyConstraint(["session_id"],["authoring_sessions.id"],name="fk_authoring_review_audit_session",ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["review_id"],["authoring_reviews.id"],name="fk_authoring_review_audit_review",ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("action IN ('review_started','review_changed','accepted','rejected')",name="ck_authoring_review_audit_action"),
        sa.CheckConstraint("review_version > 0",name="ck_authoring_review_audit_version"))
    op.create_index("ix_authoring_review_audit_session_created","authoring_review_audit",["session_id","created_at"])


def downgrade():
    op.drop_table("authoring_review_audit")
    op.drop_table("authoring_reviews")
