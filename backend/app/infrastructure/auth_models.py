"""Account and opaque-session persistence mappings.

Authentication behavior deliberately lives outside this module.  These rows only
establish the durable boundary designed in Phase B1.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models import Base, uuid_type

clock = text("clock_timestamp()")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "login = btrim(login) AND char_length(login) BETWEEN 1 AND 254",
            name="ck_users_login_valid",
        ),
        CheckConstraint(
            "normalized_login = btrim(normalized_login) AND char_length(normalized_login) BETWEEN 1 AND 254",
            name="ck_users_normalized_login_valid",
        ),
        CheckConstraint(
            "display_name = btrim(display_name) AND char_length(display_name) BETWEEN 1 AND 200",
            name="ck_users_display_name_valid",
        ),
        CheckConstraint(
            "char_length(password_hash) BETWEEN 1 AND 1024",
            name="ck_users_password_hash_valid",
        ),
        PrimaryKeyConstraint("id", name="pk_users"),
        UniqueConstraint("normalized_login", name="uq_users_normalized_login"),
    )
    id: Mapped[UUID] = mapped_column(
        uuid_type, primary_key=True, server_default=text("gen_random_uuid()")
    )
    login: Mapped[str] = mapped_column(String(254))
    normalized_login: Mapped[str] = mapped_column(String(254))
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "role", name="pk_user_roles"),
        CheckConstraint(
            "role IN ('admin','teacher','student')", name="ck_user_roles_role"
        ),
    )
    user_id: Mapped[UUID] = mapped_column(
        uuid_type,
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            name="fk_user_roles_user_id_users",
        ),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(32), primary_key=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        CheckConstraint(
            "octet_length(token_hash) = 32", name="ck_auth_sessions_token_hash_sha256"
        ),
        CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expiration"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_auth_sessions_revocation",
        ),
        Index("ix_auth_sessions_user_expires", "user_id", "expires_at"),
        Index(
            "ix_auth_sessions_active_expires",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(
        uuid_type, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        uuid_type,
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            name="fk_auth_sessions_user_id_users",
        ),
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class StudentUserLink(Base):
    __tablename__ = "student_user_links"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", name="pk_student_user_links"),
        UniqueConstraint("student_id", name="uq_student_user_links_student_id"),
    )
    user_id: Mapped[UUID] = mapped_column(
        uuid_type,
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            name="fk_student_user_links_user_id_users",
        ),
        primary_key=True,
    )
    student_id: Mapped[UUID] = mapped_column(
        uuid_type,
        ForeignKey(
            "students.id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            name="fk_student_user_links_student_id_students",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=clock
    )
