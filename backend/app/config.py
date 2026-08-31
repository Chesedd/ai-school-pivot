"""Centralized typed backend configuration."""

from datetime import timedelta
from functools import lru_cache
from uuid import UUID

from pydantic import PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings supplied by the environment of the running backend."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: PostgresDsn
    database_echo: bool = False
    content_bank_dev_actor_id: UUID
    cors_origins: str = "http://localhost:5173"
    attachment_storage_path: str = "/tmp/ai-school-pivot-attachments"
    attachment_max_bytes: int = 5 * 1024 * 1024
    artifact_storage_path: str = "/tmp/ai-school-pivot-image-artifacts"
    auth_session_ttl: timedelta = timedelta(hours=12)
    auth_session_cookie_name: str = "__Host-ai_school_session"
    auth_session_cookie_secure: bool = True
    auth_session_cookie_samesite: str = "lax"
    openai_api_key: str | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_auth_token: SecretStr | None = None
    anthropic_base_url: str | None = None
    image_solving_anthropic_model: str = "claude-sonnet-4-6"
    authoring_routes: str = "openai:gpt-4.1-mini,anthropic:claude-sonnet-4-20250514"

    @field_validator("cors_origins")
    @classmethod
    def validate_credentialed_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins or "*" in origins:
            raise ValueError("credentialed CORS requires explicit origins")
        return ",".join(origins)

    @field_validator("auth_session_cookie_samesite")
    @classmethod
    def validate_cookie_samesite(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("cookie SameSite must be lax, strict, or none")
        return normalized

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return tuple(self.cors_origins.split(","))

    @property
    def anthropic_credential(self) -> str | None:
        """Resolve gateway bearer auth first while retaining API-key compatibility."""
        value = self.anthropic_auth_token or self.anthropic_api_key
        return None if value is None else value.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide, immutable settings instance."""
    return Settings()
