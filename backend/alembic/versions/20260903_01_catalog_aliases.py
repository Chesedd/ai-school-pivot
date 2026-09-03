"""Human-confirmed curriculum catalog aliases.

Revision ID: 20260903_01
Revises: 20260901_02
"""
from alembic import op
import sqlalchemy as sa

revision="20260903_01"; down_revision="20260901_02"; branch_labels=None; depends_on=None

def upgrade():
    op.create_table("curriculum_catalog_aliases",
        sa.Column("id",sa.Uuid(),server_default=sa.text("gen_random_uuid()"),nullable=False),
        sa.Column("kind",sa.String(16),nullable=False),sa.Column("alias_name",sa.Text(),nullable=False),
        sa.Column("normalized_alias",sa.Text(),nullable=False),
        *[sa.Column(f"{x}_target_id",sa.Uuid(),nullable=True) for x in ("subject","topic","subtopic","skill")],
        sa.Column("subject_id",sa.Uuid(),nullable=True),sa.Column("grade_id",sa.Uuid(),nullable=True),
        sa.Column("topic_id",sa.Uuid(),nullable=True),sa.Column("subtopic_id",sa.Uuid(),nullable=True),
        sa.Column("created_by",sa.Uuid(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("clock_timestamp()"),nullable=False),
        sa.PrimaryKeyConstraint("id",name="pk_curriculum_catalog_aliases"),
        sa.CheckConstraint("kind IN ('subject','topic','subtopic','skill')",name="ck_catalog_aliases_kind"),
        sa.CheckConstraint("char_length(normalized_alias) BETWEEN 1 AND 200 AND alias_name=btrim(alias_name) AND char_length(alias_name) BETWEEN 1 AND 200",name="ck_catalog_aliases_names"),
        sa.CheckConstraint("num_nonnulls(subject_target_id,topic_target_id,subtopic_target_id,skill_target_id)=1",name="ck_catalog_aliases_one_target"),
        sa.CheckConstraint("(kind='subject' AND subject_target_id IS NOT NULL AND subject_id IS NULL AND grade_id IS NULL AND topic_id IS NULL AND subtopic_id IS NULL) OR (kind='topic' AND topic_target_id IS NOT NULL AND subject_id IS NOT NULL AND grade_id IS NOT NULL AND topic_id IS NULL AND subtopic_id IS NULL) OR (kind='subtopic' AND subtopic_target_id IS NOT NULL AND topic_id IS NOT NULL AND subject_id IS NULL AND grade_id IS NULL AND subtopic_id IS NULL) OR (kind='skill' AND skill_target_id IS NOT NULL AND subtopic_id IS NOT NULL AND subject_id IS NULL AND grade_id IS NULL AND topic_id IS NULL)",name="ck_catalog_aliases_scope"),
        *[sa.ForeignKeyConstraint([f"{x}_target_id"],[f"{x}s.id"],ondelete="RESTRICT") for x in ("subject","topic","subtopic","skill")],
        sa.ForeignKeyConstraint(["subject_id"],["subjects.id"],ondelete="RESTRICT"),sa.ForeignKeyConstraint(["grade_id"],["grades.id"],ondelete="RESTRICT"),sa.ForeignKeyConstraint(["topic_id"],["topics.id"],ondelete="RESTRICT"),sa.ForeignKeyConstraint(["subtopic_id"],["subtopics.id"],ondelete="RESTRICT"),sa.ForeignKeyConstraint(["created_by"],["users.id"],ondelete="RESTRICT"))
    op.create_index("uq_catalog_alias_subject","curriculum_catalog_aliases",["normalized_alias"],unique=True,postgresql_where=sa.text("kind='subject'"))
    op.create_index("uq_catalog_alias_topic","curriculum_catalog_aliases",["normalized_alias","subject_id","grade_id"],unique=True,postgresql_where=sa.text("kind='topic'"))
    op.create_index("uq_catalog_alias_subtopic","curriculum_catalog_aliases",["normalized_alias","topic_id"],unique=True,postgresql_where=sa.text("kind='subtopic'"))
    op.create_index("uq_catalog_alias_skill","curriculum_catalog_aliases",["normalized_alias","subtopic_id"],unique=True,postgresql_where=sa.text("kind='skill'"))

def downgrade(): op.drop_table("curriculum_catalog_aliases")
