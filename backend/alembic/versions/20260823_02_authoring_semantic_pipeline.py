"""Persist bounded semantic authoring artifacts.

Revision ID: 20260823_02
Revises: 20260823_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="20260823_02"
down_revision="20260823_01"
branch_labels=None
depends_on=None

def upgrade():
    for name in ("generator_route","solver_route","generated_draft","solver_result","validation_result"):
        op.add_column("authoring_sessions",sa.Column(name,postgresql.JSONB(),nullable=True))
    op.add_column("authoring_sessions",sa.Column("pipeline_identity",sa.String(64),nullable=True))
    op.add_column("authoring_sessions",sa.Column("semantic_status",sa.String(64),nullable=True))
    op.add_column("authoring_sessions",sa.Column("generator_attempt_id",postgresql.UUID(as_uuid=True),nullable=True))
    op.add_column("authoring_sessions",sa.Column("solver_attempt_id",postgresql.UUID(as_uuid=True),nullable=True))
    op.create_check_constraint("ck_authoring_sessions_pipeline_identity","authoring_sessions","pipeline_identity IS NULL OR pipeline_identity ~ '^[0-9a-f]{64}$'")
    op.create_foreign_key("fk_authoring_sessions_generator_attempt","authoring_sessions","authoring_provider_attempts",["generator_attempt_id"],["id"],ondelete="RESTRICT")
    op.create_foreign_key("fk_authoring_sessions_solver_attempt","authoring_sessions","authoring_provider_attempts",["solver_attempt_id"],["id"],ondelete="RESTRICT")

def downgrade():
    op.drop_constraint("fk_authoring_sessions_solver_attempt","authoring_sessions",type_="foreignkey")
    op.drop_constraint("fk_authoring_sessions_generator_attempt","authoring_sessions",type_="foreignkey")
    op.drop_constraint("ck_authoring_sessions_pipeline_identity","authoring_sessions",type_="check")
    for name in ("solver_attempt_id","generator_attempt_id","semantic_status","pipeline_identity","validation_result","solver_result","generated_draft","solver_route","generator_route"):
        op.drop_column("authoring_sessions",name)
