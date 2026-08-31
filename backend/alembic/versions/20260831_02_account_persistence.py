"""Add account persistence foundation.

Revision ID: 20260831_02
Revises: 20260831_01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260831_02"
down_revision = "20260831_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("login", sa.String(254), nullable=False),
        sa.Column("normalized_login", sa.String(254), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("normalized_login", name="uq_users_normalized_login"),
        sa.CheckConstraint(
            "login = btrim(login) AND char_length(login) BETWEEN 1 AND 254",
            name="ck_users_login_valid",
        ),
        sa.CheckConstraint(
            "normalized_login = btrim(normalized_login) AND char_length(normalized_login) BETWEEN 1 AND 254",
            name="ck_users_normalized_login_valid",
        ),
        sa.CheckConstraint(
            "display_name = btrim(display_name) AND char_length(display_name) BETWEEN 1 AND 200",
            name="ck_users_display_name_valid",
        ),
        sa.CheckConstraint(
            "char_length(password_hash) BETWEEN 1 AND 1024",
            name="ck_users_password_hash_valid",
        ),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "role", name="pk_user_roles"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_roles_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint(
            "role IN ('admin','teacher','student')", name="ck_user_roles_role"
        ),
    )
    op.create_table(
        "auth_sessions",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_auth_sessions_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32", name="ck_auth_sessions_token_hash_sha256"
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_auth_sessions_expiration"
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_auth_sessions_revocation",
        ),
    )
    op.create_index(
        "ix_auth_sessions_user_expires", "auth_sessions", ["user_id", "expires_at"]
    )
    op.create_index(
        "ix_auth_sessions_active_expires",
        "auth_sessions",
        ["expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "student_user_links",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_student_user_links"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_student_user_links_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name="fk_student_user_links_student_id_students",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.UniqueConstraint("student_id", name="uq_student_user_links_student_id"),
    )


def downgrade():
    op.drop_table("student_user_links")
    op.drop_index("ix_auth_sessions_active_expires", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_expires", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("user_roles")
    op.drop_table("users")
