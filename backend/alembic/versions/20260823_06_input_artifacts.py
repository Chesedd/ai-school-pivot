"""Add immutable authoring input artifacts and optional session association.

Revision ID: 20260823_06
Revises: 20260823_05
"""
from alembic import op
import sqlalchemy as sa

revision = "20260823_06"
down_revision = "20260823_05"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("input_artifacts",
        sa.Column("id",sa.Uuid(),server_default=sa.text("gen_random_uuid()"),nullable=False),
        sa.Column("owner_id",sa.Uuid(),nullable=False),
        sa.Column("mime_type",sa.String(32),nullable=False),
        sa.Column("content_hash_sha256",sa.String(64),nullable=False),
        sa.Column("size_bytes",sa.Integer(),nullable=False),
        sa.Column("storage_reference",sa.String(512),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("clock_timestamp()"),nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_reference",name="uq_input_artifacts_storage_reference"),
        sa.UniqueConstraint("id","owner_id",name="uq_input_artifacts_id_owner"),
        sa.CheckConstraint("mime_type IN ('image/png','image/jpeg','image/webp','application/pdf')",name="ck_input_artifacts_mime"),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 26214400",name="ck_input_artifacts_size"),
        sa.CheckConstraint("content_hash_sha256 ~ '^[0-9a-f]{64}$'",name="ck_input_artifacts_hash"),
        sa.CheckConstraint("storage_reference=btrim(storage_reference) AND char_length(storage_reference) BETWEEN 1 AND 512",name="ck_input_artifacts_storage_reference"))
    op.create_index("ix_input_artifacts_owner_created","input_artifacts",["owner_id","created_at"])
    op.add_column("authoring_sessions",sa.Column("input_artifact_id",sa.Uuid(),nullable=True))
    op.create_foreign_key("fk_authoring_sessions_owned_input_artifact","authoring_sessions","input_artifacts",
        ["input_artifact_id","owner_id"],["id","owner_id"],ondelete="RESTRICT")
    op.execute("""CREATE FUNCTION reject_input_artifact_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'input artifacts are immutable';
        END $$""")
    op.execute("""CREATE TRIGGER trg_input_artifacts_immutable BEFORE UPDATE OR DELETE
        ON input_artifacts FOR EACH ROW EXECUTE FUNCTION reject_input_artifact_mutation()""")


def downgrade():
    op.drop_constraint("fk_authoring_sessions_owned_input_artifact","authoring_sessions",type_="foreignkey")
    op.drop_column("authoring_sessions","input_artifact_id")
    op.execute("DROP TRIGGER trg_input_artifacts_immutable ON input_artifacts")
    op.execute("DROP FUNCTION reject_input_artifact_mutation()")
    op.drop_table("input_artifacts")
