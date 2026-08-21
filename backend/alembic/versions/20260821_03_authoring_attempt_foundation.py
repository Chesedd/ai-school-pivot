"""Content Bank authoring session and durable attempt foundation.

Revision ID: 20260821_03
Revises: 20260821_02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="20260821_03"; down_revision="20260821_02"; branch_labels=None; depends_on=None


def upgrade():
    sa.Enum("draft","generating","ready","confirmed","rejected","expired",name="authoring_session_status").create(op.get_bind())
    sa.Enum("pending","running","succeeded","invalid_output","failed_retryable","failed_terminal",name="authoring_attempt_status").create(op.get_bind())
    sa.Enum("generator","solver",name="authoring_role").create(op.get_bind())
    op.create_table("authoring_sessions",
        sa.Column("owner_id",sa.Uuid(),nullable=False),sa.Column("schema_version",sa.String(64),nullable=False),sa.Column("policy_version",sa.String(128),nullable=False),
        sa.Column("frozen_request",postgresql.JSONB(),nullable=False),sa.Column("request_fingerprint",sa.String(64),nullable=False),sa.Column("frozen_allowlist",postgresql.JSONB(),nullable=False),
        sa.Column("status",postgresql.ENUM(name="authoring_session_status",create_type=False),server_default=sa.text("'draft'::authoring_session_status"),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("clock_timestamp()"),nullable=False),sa.Column("row_version",sa.Integer(),server_default="1",nullable=False),sa.Column("id",sa.Uuid(),server_default=sa.text("gen_random_uuid()"),nullable=False),sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'",name="ck_authoring_sessions_fingerprint"),sa.CheckConstraint("char_length(schema_version) BETWEEN 1 AND 64 AND char_length(policy_version) BETWEEN 1 AND 128",name="ck_authoring_sessions_versions"),sa.CheckConstraint("row_version > 0",name="ck_authoring_sessions_revision"))
    op.create_index("ix_authoring_sessions_owner_status","authoring_sessions",["owner_id","status"]); op.create_index("ix_authoring_sessions_status_created","authoring_sessions",["status","created_at"])
    op.create_table("authoring_provider_attempts",
        sa.Column("session_id",sa.Uuid(),nullable=False),sa.Column("role",postgresql.ENUM(name="authoring_role",create_type=False),nullable=False),sa.Column("attempt_number",sa.Integer(),nullable=False),sa.Column("idempotency_key",sa.String(128),nullable=False),
        sa.Column("provider_id",sa.String(128),nullable=False),sa.Column("model_id",sa.String(128),nullable=False),sa.Column("settings_snapshot",postgresql.JSONB(),nullable=False),sa.Column("prompt_snapshot",postgresql.JSONB(),nullable=False),sa.Column("request_fingerprint",sa.String(64),nullable=False),sa.Column("timeout_ms",sa.Integer(),nullable=False),
        sa.Column("status",postgresql.ENUM(name="authoring_attempt_status",create_type=False),server_default=sa.text("'pending'::authoring_attempt_status"),nullable=False),sa.Column("failure_code",sa.String(64)),sa.Column("provider_request_id",sa.String(256)),sa.Column("response_hash",sa.String(64)),sa.Column("latency_ms",sa.Integer()),
        sa.Column("input_tokens",sa.Integer(),server_default="0",nullable=False),sa.Column("output_tokens",sa.Integer(),server_default="0",nullable=False),sa.Column("cached_tokens",sa.Integer(),server_default="0",nullable=False),sa.Column("cost_amount",sa.Numeric(18,8)),sa.Column("currency",sa.String(3)),sa.Column("pricing_version",sa.String(128)),sa.Column("pricing_source",sa.String(128)),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("clock_timestamp()"),nullable=False),sa.Column("started_at",sa.DateTime(timezone=True)),sa.Column("finished_at",sa.DateTime(timezone=True)),sa.Column("id",sa.Uuid(),server_default=sa.text("gen_random_uuid()"),nullable=False),
        sa.ForeignKeyConstraint(["session_id"],["authoring_sessions.id"],name="fk_authoring_attempts_session",ondelete="RESTRICT"),sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id","idempotency_key",name="uq_authoring_attempts_session_key"),sa.UniqueConstraint("session_id","role","attempt_number",name="uq_authoring_attempts_number"),
        sa.CheckConstraint("attempt_number > 0 AND timeout_ms BETWEEN 1 AND 120000 AND input_tokens BETWEEN 0 AND 10000000 AND output_tokens BETWEEN 0 AND 10000000 AND cached_tokens BETWEEN 0 AND 10000000",name="ck_authoring_attempts_numbers"),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$' AND (response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$')",name="ck_authoring_attempts_hashes"),sa.CheckConstraint("cost_amount IS NULL OR cost_amount >= 0",name="ck_authoring_attempts_cost"),sa.CheckConstraint("provider_id=btrim(provider_id) AND char_length(provider_id) BETWEEN 1 AND 128 AND model_id=btrim(model_id) AND char_length(model_id) BETWEEN 1 AND 128 AND idempotency_key=btrim(idempotency_key) AND char_length(idempotency_key) BETWEEN 1 AND 128",name="ck_authoring_attempts_ids"),sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0",name="ck_authoring_attempts_latency"),sa.CheckConstraint("finished_at IS NULL OR finished_at >= started_at",name="ck_authoring_attempts_time"),sa.CheckConstraint("(cost_amount IS NULL AND currency IS NULL AND pricing_version IS NULL AND pricing_source IS NULL) OR (cost_amount IS NOT NULL AND currency ~ '^[A-Z]{3}$' AND char_length(pricing_version) BETWEEN 1 AND 128 AND char_length(pricing_source) BETWEEN 1 AND 128)",name="ck_authoring_attempts_cost_metadata"),
        sa.CheckConstraint("(status='pending' AND started_at IS NULL AND finished_at IS NULL) OR (status='running' AND started_at IS NOT NULL AND finished_at IS NULL) OR (status IN ('succeeded','invalid_output','failed_retryable','failed_terminal') AND started_at IS NOT NULL AND finished_at IS NOT NULL)",name="ck_authoring_attempts_lifecycle"),sa.CheckConstraint("(status IN ('failed_retryable','failed_terminal') AND failure_code IS NOT NULL) OR (status NOT IN ('failed_retryable','failed_terminal') AND failure_code IS NULL)",name="ck_authoring_attempts_failure"))
    op.create_index("ix_authoring_attempts_session_status","authoring_provider_attempts",["session_id","status"]); op.create_index("ix_authoring_attempts_claim","authoring_provider_attempts",["status","created_at"]); op.create_index("ix_authoring_attempts_provider_request","authoring_provider_attempts",["provider_request_id"])


def downgrade():
    op.drop_table("authoring_provider_attempts"); op.drop_table("authoring_sessions")
    sa.Enum(name="authoring_role").drop(op.get_bind()); sa.Enum(name="authoring_attempt_status").drop(op.get_bind()); sa.Enum(name="authoring_session_status").drop(op.get_bind())
