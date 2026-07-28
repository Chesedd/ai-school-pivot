"""Store one-time Content Bank import previews."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision="20260728_02"
down_revision="20260728_01"
branch_labels=None
depends_on=None
def upgrade():
    op.create_table("import_previews",sa.Column("import_token",postgresql.UUID(as_uuid=True),primary_key=True),sa.Column("format",sa.String(8),nullable=False),sa.Column("actor_id",postgresql.UUID(as_uuid=True),nullable=False),sa.Column("rows",postgresql.JSONB(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),sa.Column("committed_at",sa.DateTime(timezone=True),nullable=True),sa.CheckConstraint("format IN ('csv','xlsx')",name="ck_import_previews_format"),sa.CheckConstraint("expires_at > created_at",name="ck_import_previews_expiry"),sa.CheckConstraint("committed_at IS NULL OR committed_at >= created_at",name="ck_import_previews_committed_at"))
    op.create_index("ix_import_previews_expires_at","import_previews",["expires_at"])
def downgrade(): op.drop_table("import_previews")
