"""Paper-only limits; PAPER_<FIELD> environment variables configure deployment.

Existing 25 MiB source and 25 megapixel security ceilings cannot be weakened.
Paper claims use both batch row locks and a database-wide advisory claim lock.
"""
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.application.input_artifacts import MAX_ARTIFACT_SIZE_BYTES

PAPER_PROVIDER_TIMEOUT_SECONDS = 30
MAX_SECURITY_PAGE_PIXELS = 25_000_000
MAX_SECURITY_PDF_PAGES = 200
# Separate two-int PostgreSQL advisory-lock namespace for paper provider claims.
PAPER_CHECK_GLOBAL_LOCK_KEY = (0x50415052, 13)


class PaperOperationalLimits(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAPER_", extra="ignore", frozen=True)

    max_artifacts_per_batch: int = Field(default=25, gt=0)
    max_total_upload_bytes_per_batch: int = Field(default=250 * 1024 * 1024, gt=0)
    max_single_artifact_bytes: int = Field(default=MAX_ARTIFACT_SIZE_BYTES, gt=0, le=MAX_ARTIFACT_SIZE_BYTES)
    max_pages_per_artifact: int = Field(default=100, gt=0, le=MAX_SECURITY_PDF_PAGES)
    max_pages_per_batch: int = Field(default=150, gt=0)
    max_pixels_per_page: int = Field(default=MAX_SECURITY_PAGE_PIXELS, gt=0, le=MAX_SECURITY_PAGE_PIXELS)
    max_paper_submissions_per_batch: int = Field(default=60, gt=0)
    max_pages_per_paper: int = Field(default=20, gt=0)
    max_parallel_matching_runs: int = Field(default=1, ge=1, le=1)
    max_parallel_paper_checks_per_batch: int = Field(default=3, gt=0)
    max_parallel_paper_checks_global: int = Field(default=12, gt=0)
    max_provider_pages_per_paper: int = Field(default=20, gt=0)
    max_provider_image_bytes_per_paper: int = Field(default=25 * 1024 * 1024, gt=0)
    execution_lease_seconds: int = Field(default=120, ge=2 * PAPER_PROVIDER_TIMEOUT_SECONDS)

    @model_validator(mode="after")
    def relationships(self):
        if self.max_single_artifact_bytes > self.max_total_upload_bytes_per_batch:
            raise ValueError("single artifact bytes exceed batch bytes")
        if self.max_provider_pages_per_paper > self.max_pages_per_paper:
            raise ValueError("provider pages exceed pages per paper")
        if self.max_pages_per_artifact > self.max_pages_per_batch:
            raise ValueError("artifact pages exceed batch pages")
        return self

    def guard(self, field: str, value: int, code: str, error):
        if value > getattr(self, field):
            raise error(code)
