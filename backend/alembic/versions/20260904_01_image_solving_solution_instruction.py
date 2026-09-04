"""Persist teacher solution instructions. Revision ID: 20260904_01 Revises: 20260903_01"""
from alembic import op
import sqlalchemy as sa
revision="20260904_01"; down_revision="20260903_01"; branch_labels=None; depends_on=None
def upgrade():
    op.add_column("image_solving_sessions",sa.Column("solution_instruction",sa.Text(),nullable=True))
    op.create_check_constraint("ck_image_solving_sessions_solution_instruction","image_solving_sessions","solution_instruction IS NULL OR (solution_instruction = btrim(solution_instruction) AND char_length(solution_instruction) BETWEEN 1 AND 4000)")
def downgrade():
    op.drop_constraint("ck_image_solving_sessions_solution_instruction","image_solving_sessions",type_="check")
    op.drop_column("image_solving_sessions","solution_instruction")
