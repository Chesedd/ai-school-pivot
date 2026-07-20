"""Centralized typed backend configuration."""

from functools import lru_cache

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings supplied by the environment of the running backend."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: PostgresDsn
    database_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide, immutable settings instance."""
    return Settings()
