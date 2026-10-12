"""Bounded operator-only scanned-paper storage lifecycle."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

POLICY_VERSION = "paper_storage_retention.v2"


class PaperRetentionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAPER_", extra="ignore", frozen=True)

    source_retention_days: int = Field(default=30, gt=0, le=3650)
    unpublished_page_retention_days: int = Field(default=30, gt=0, le=3650)
    withdrawal_page_grace_days: int = Field(default=30, gt=0, le=3650)
    retention_max_targets_per_run: int = Field(default=100, gt=0, le=1000)
    retention_claim_ttl_seconds: int = Field(default=300, ge=30, le=3600)


@dataclass(frozen=True)
class RetentionTarget:
    batch_id: UUID
    target_kind: str
    target_id: UUID
    storage_reference: str
    eligible_at: datetime


class ScanStorageRetentionService:
    def __init__(self, repository, storage, settings=None):
        self.repository, self.storage = repository, storage
        self.settings = settings or PaperRetentionSettings()

    async def run(self, *, dry_run=True):
        targets = await self.repository.discover(self.settings)
        report = {
            "dry_run": dry_run,
            "selected": len(targets),
            "purged": 0,
            "retryable_failed": 0,
            "skipped": 0,
            "targets": [
                {
                    "kind": t.target_kind,
                    "id": str(t.target_id),
                    "batch_id": str(t.batch_id),
                }
                for t in targets
            ],
        }
        if dry_run:
            return report
        for target in targets:
            claim = await self.repository.claim(target, self.settings)
            if claim is None:
                report["skipped"] += 1
                continue
            error = None
            try:
                await self.storage.delete(target.storage_reference)
            except FileNotFoundError:
                pass  # Delete succeeded previously, before durable finalization.
            except Exception:  # noqa: BLE001 - diverse storage errors are bounded here.
                error = "storage_delete_failed"
            if await self.repository.finalize(claim, error):
                report["retryable_failed" if error else "purged"] += 1
            else:
                report["skipped"] += 1  # An expired owner cannot finalize a new lease.
        return report
