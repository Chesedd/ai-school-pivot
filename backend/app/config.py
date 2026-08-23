"""Centralized typed backend configuration."""

from functools import lru_cache
from uuid import UUID

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings supplied by the environment of the running backend."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: PostgresDsn
    database_echo: bool = False
    content_bank_dev_actor_id: UUID
    assessment_dev_student_id: UUID
    cors_origins: str = "http://localhost:5173"
    attachment_storage_path: str = "/tmp/ai-school-pivot-attachments"
    attachment_max_bytes: int = 5 * 1024 * 1024
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide, immutable settings instance."""
    return Settings()
