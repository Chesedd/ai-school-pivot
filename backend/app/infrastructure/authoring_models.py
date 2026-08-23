"""Content Bank-owned durable authoring session and provider attempt mappings."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models import Base, IdMixin, uuid_type

clock=text("clock_timestamp()")
session_status=ENUM("draft","generating","ready","confirmed","rejected","expired",name="authoring_session_status",create_type=False)
attempt_status=ENUM("pending","running","succeeded","invalid_output","failed_retryable","failed_terminal",name="authoring_attempt_status",create_type=False)
role_type=ENUM("generator","solver",name="authoring_role",create_type=False)


class AuthoringSession(IdMixin,Base):
    __tablename__="authoring_sessions"
    __table_args__=(
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'",name="ck_authoring_sessions_fingerprint"),
        CheckConstraint("char_length(schema_version) BETWEEN 1 AND 64 AND char_length(policy_version) BETWEEN 1 AND 128",name="ck_authoring_sessions_versions"),
        CheckConstraint("row_version > 0",name="ck_authoring_sessions_revision"),
        ForeignKeyConstraint(["input_artifact_id", "owner_id"], ["input_artifacts.id", "input_artifacts.owner_id"],
            name="fk_authoring_sessions_owned_input_artifact", ondelete="RESTRICT"),
        Index("ix_authoring_sessions_owner_status","owner_id","status"), Index("ix_authoring_sessions_status_created","status","created_at"),)
    owner_id: Mapped[UUID]=mapped_column(uuid_type)
    schema_version: Mapped[str]=mapped_column(String(64)); policy_version: Mapped[str]=mapped_column(String(128))
    frozen_request: Mapped[object]=mapped_column(JSONB); request_fingerprint: Mapped[str]=mapped_column(String(64))
    frozen_allowlist: Mapped[object]=mapped_column(JSONB); status: Mapped[str]=mapped_column(session_status,server_default=text("'draft'::authoring_session_status"))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=clock); row_version: Mapped[int]=mapped_column(Integer,server_default="1")
    pipeline_identity: Mapped[str|None]=mapped_column(String(64)); generator_route: Mapped[object|None]=mapped_column(JSONB)
    solver_route: Mapped[object|None]=mapped_column(JSONB); generated_draft: Mapped[object|None]=mapped_column(JSONB)
    solver_result: Mapped[object|None]=mapped_column(JSONB); validation_result: Mapped[object|None]=mapped_column(JSONB)
    semantic_status: Mapped[str|None]=mapped_column(String(64))
    generator_attempt_id: Mapped[UUID|None]=mapped_column(uuid_type); solver_attempt_id: Mapped[UUID|None]=mapped_column(uuid_type)
    input_artifact_id: Mapped[UUID|None]=mapped_column(uuid_type)


class InputArtifact(IdMixin, Base):
    """Immutable authoring metadata; raw bytes are never persisted here."""
    __tablename__ = "input_artifacts"
    __table_args__ = (
        CheckConstraint("mime_type IN ('image/png','image/jpeg','image/webp','application/pdf')", name="ck_input_artifacts_mime"),
        CheckConstraint("size_bytes BETWEEN 1 AND 26214400", name="ck_input_artifacts_size"),
        CheckConstraint("content_hash_sha256 ~ '^[0-9a-f]{64}$'", name="ck_input_artifacts_hash"),
        CheckConstraint("storage_reference=btrim(storage_reference) AND char_length(storage_reference) BETWEEN 1 AND 512", name="ck_input_artifacts_storage_reference"),
        UniqueConstraint("storage_reference", name="uq_input_artifacts_storage_reference"),
        UniqueConstraint("id", "owner_id", name="uq_input_artifacts_id_owner"),
        Index("ix_input_artifacts_owner_created", "owner_id", "created_at"),
    )
    owner_id: Mapped[UUID] = mapped_column(uuid_type)
    mime_type: Mapped[str] = mapped_column(String(32))
    content_hash_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_reference: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock)


class AuthoringProviderAttempt(IdMixin,Base):
    __tablename__="authoring_provider_attempts"
    __table_args__=(
        UniqueConstraint("session_id","idempotency_key",name="uq_authoring_attempts_session_key"),
        UniqueConstraint("session_id","role","attempt_number",name="uq_authoring_attempts_number"),
        CheckConstraint("attempt_number > 0 AND timeout_ms BETWEEN 1 AND 120000 AND input_tokens BETWEEN 0 AND 10000000 AND output_tokens BETWEEN 0 AND 10000000 AND cached_tokens BETWEEN 0 AND 10000000",name="ck_authoring_attempts_numbers"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$' AND (response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$')",name="ck_authoring_attempts_hashes"),
        CheckConstraint("cost_amount IS NULL OR cost_amount >= 0",name="ck_authoring_attempts_cost"),
        CheckConstraint("provider_id=btrim(provider_id) AND char_length(provider_id) BETWEEN 1 AND 128 AND model_id=btrim(model_id) AND char_length(model_id) BETWEEN 1 AND 128 AND idempotency_key=btrim(idempotency_key) AND char_length(idempotency_key) BETWEEN 1 AND 128",name="ck_authoring_attempts_ids"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0",name="ck_authoring_attempts_latency"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at",name="ck_authoring_attempts_time"),
        CheckConstraint("(cost_amount IS NULL AND currency IS NULL AND pricing_version IS NULL AND pricing_source IS NULL) OR (cost_amount IS NOT NULL AND currency ~ '^[A-Z]{3}$' AND char_length(pricing_version) BETWEEN 1 AND 128 AND char_length(pricing_source) BETWEEN 1 AND 128)",name="ck_authoring_attempts_cost_metadata"),
        CheckConstraint("(status='pending' AND started_at IS NULL AND finished_at IS NULL) OR (status='running' AND started_at IS NOT NULL AND finished_at IS NULL) OR (status IN ('succeeded','invalid_output','failed_retryable','failed_terminal') AND started_at IS NOT NULL AND finished_at IS NOT NULL)",name="ck_authoring_attempts_lifecycle"),
        CheckConstraint("(status IN ('failed_retryable','failed_terminal') AND failure_code IS NOT NULL) OR (status NOT IN ('failed_retryable','failed_terminal') AND failure_code IS NULL)",name="ck_authoring_attempts_failure"),
        Index("ix_authoring_attempts_session_status","session_id","status"), Index("ix_authoring_attempts_claim","status","created_at"), Index("ix_authoring_attempts_provider_request","provider_request_id"),)
    session_id: Mapped[UUID]=mapped_column(ForeignKey("authoring_sessions.id",ondelete="RESTRICT",name="fk_authoring_attempts_session"))
    role: Mapped[str]=mapped_column(role_type); attempt_number: Mapped[int]=mapped_column(Integer); idempotency_key: Mapped[str]=mapped_column(String(128))
    provider_id: Mapped[str]=mapped_column(String(128)); model_id: Mapped[str]=mapped_column(String(128)); settings_snapshot: Mapped[object]=mapped_column(JSONB)
    prompt_snapshot: Mapped[object]=mapped_column(JSONB); request_fingerprint: Mapped[str]=mapped_column(String(64)); timeout_ms: Mapped[int]=mapped_column(Integer)
    status: Mapped[str]=mapped_column(attempt_status,server_default=text("'pending'::authoring_attempt_status")); failure_code: Mapped[str|None]=mapped_column(String(64)); provider_request_id: Mapped[str|None]=mapped_column(String(256)); response_hash: Mapped[str|None]=mapped_column(String(64)); latency_ms: Mapped[int|None]=mapped_column(Integer)
    input_tokens: Mapped[int]=mapped_column(Integer,server_default="0"); output_tokens: Mapped[int]=mapped_column(Integer,server_default="0"); cached_tokens: Mapped[int]=mapped_column(Integer,server_default="0")
    cache_read_tokens: Mapped[int]=mapped_column(Integer,server_default="0"); cache_write_tokens: Mapped[int]=mapped_column(Integer,server_default="0")
    cost_amount: Mapped[Decimal|None]=mapped_column(Numeric(18,8)); currency: Mapped[str|None]=mapped_column(String(3)); pricing_version: Mapped[str|None]=mapped_column(String(128)); pricing_source: Mapped[str|None]=mapped_column(String(128))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=clock); started_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); finished_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))


class AuthoringReview(IdMixin, Base):
    """Mutable human-owned copy; the generated checkpoint remains immutable."""
    __tablename__ = "authoring_reviews"
    __table_args__ = (
        CheckConstraint("state IN ('reviewing','accepted','rejected')", name="ck_authoring_reviews_state"),
        CheckConstraint("version > 0", name="ck_authoring_reviews_version"),
        UniqueConstraint("session_id", name="uq_authoring_reviews_session"),
        Index("ix_authoring_reviews_session_state", "session_id", "state"),
    )
    session_id: Mapped[UUID] = mapped_column(ForeignKey("authoring_sessions.id", ondelete="RESTRICT", name="fk_authoring_reviews_session"))
    owner_id: Mapped[UUID] = mapped_column(uuid_type)
    state: Mapped[str] = mapped_column(String(16), server_default="reviewing")
    draft: Mapped[object] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock)
    accepted_revision_id: Mapped[UUID | None] = mapped_column(ForeignKey(
        "authoring_review_revisions.id", ondelete="RESTRICT", name="fk_authoring_reviews_accepted_revision",
        use_alter=True))


class AuthoringReviewRevision(IdMixin, Base):
    """Append-only snapshot of the reviewer-visible artifact (never provider data)."""
    __tablename__ = "authoring_review_revisions"
    __table_args__ = (
        CheckConstraint("revision_number >= 0", name="ck_authoring_review_revisions_number"),
        UniqueConstraint("session_id", "revision_number", name="uq_authoring_review_revisions_number"),
        Index("ix_authoring_review_revisions_session_number", "session_id", "revision_number"),
    )
    session_id: Mapped[UUID] = mapped_column(ForeignKey("authoring_sessions.id", ondelete="RESTRICT", name="fk_authoring_review_revisions_session"))
    review_id: Mapped[UUID] = mapped_column(ForeignKey("authoring_reviews.id", ondelete="RESTRICT", name="fk_authoring_review_revisions_review"))
    revision_number: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[object] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock)
    actor_id: Mapped[UUID] = mapped_column(uuid_type)
    change_summary: Mapped[object] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class AuthoringReviewAudit(IdMixin, Base):
    __tablename__ = "authoring_review_audit"
    __table_args__ = (
        CheckConstraint("action IN ('review_started','review_changed','review_revision_created','review_revision_accepted','quality_report_created','warning_overridden','accepted','rejected')", name="ck_authoring_review_audit_action"),
        CheckConstraint("review_version > 0", name="ck_authoring_review_audit_version"),
        Index("ix_authoring_review_audit_session_created", "session_id", "created_at"),
    )
    session_id: Mapped[UUID] = mapped_column(ForeignKey("authoring_sessions.id", ondelete="RESTRICT", name="fk_authoring_review_audit_session"))
    review_id: Mapped[UUID] = mapped_column(ForeignKey("authoring_reviews.id", ondelete="RESTRICT", name="fk_authoring_review_audit_review"))
    actor_id: Mapped[UUID] = mapped_column(uuid_type)
    action: Mapped[str] = mapped_column(String(32))
    review_version: Mapped[int] = mapped_column(Integer)
    details: Mapped[object] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=clock)
