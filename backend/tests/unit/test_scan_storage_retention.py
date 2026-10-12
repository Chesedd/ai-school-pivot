"""Retention orchestration and real opaque filesystem boundary."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.application.scan_storage_retention import (
    POLICY_VERSION,
    PaperRetentionSettings,
    RetentionTarget,
    ScanStorageRetentionService,
)
from app.config import Settings
from app.infrastructure.artifact_storage import FilesystemArtifactStorage
from pydantic import ValidationError


@pytest.mark.parametrize("field", list(PaperRetentionSettings.model_fields))
@pytest.mark.parametrize("value", [0, -1, 100000])
def test_invalid_settings_rejected(field, value):
    with pytest.raises(ValidationError):
        PaperRetentionSettings(**{field: value})


@pytest.mark.parametrize("field", list(PaperRetentionSettings.model_fields))
def test_environment_settings_validate_at_startup(monkeypatch, field):
    monkeypatch.setenv("PAPER_" + field.upper(), "40")
    assert (
        getattr(
            Settings(
                database_url="postgresql+asyncpg://localhost/test"
            ).paper_retention,
            field,
        )
        == 40
    )
    monkeypatch.setenv("PAPER_" + field.upper(), "0")
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+asyncpg://localhost/test")


def test_defaults_and_policy_are_stable():
    assert PaperRetentionSettings().model_dump() == {
        "source_retention_days": 30,
        "unpublished_page_retention_days": 30,
        "withdrawal_page_grace_days": 30,
        "retention_max_targets_per_run": 100,
        "retention_claim_ttl_seconds": 300,
    }
    assert POLICY_VERSION == "paper_storage_retention.v2"


def repository():
    target = RetentionTarget(
        uuid4(), "source_artifact", uuid4(), str(uuid4()), datetime.now(UTC)
    )
    repo = AsyncMock()
    repo.discover.return_value = [target]
    repo.claim.return_value = (uuid4(), uuid4())
    repo.finalize.return_value = True
    return repo, target


async def test_dry_run_has_no_claim_finalize_or_storage_mutation():
    repo, _ = repository()
    storage = AsyncMock()
    report = await ScanStorageRetentionService(repo, storage).run()
    assert report["dry_run"] and report["selected"] == 1
    repo.claim.assert_not_called()
    repo.finalize.assert_not_called()
    storage.delete.assert_not_called()
    assert "storage_reference" not in str(report)


@pytest.mark.parametrize(
    "error", [None, FileNotFoundError(), OSError("private backend details")]
)
async def test_storage_outcomes_are_bounded(error):
    repo, target = repository()
    storage = AsyncMock()
    storage.delete.side_effect = error
    report = await ScanStorageRetentionService(repo, storage).run(dry_run=False)
    storage.delete.assert_awaited_once_with(target.storage_reference)
    failure = (
        "storage_delete_failed"
        if isinstance(error, OSError) and not isinstance(error, FileNotFoundError)
        else None
    )
    repo.finalize.assert_awaited_once_with(repo.claim.return_value, failure)
    assert report["retryable_failed"] == int(failure is not None)
    assert report["purged"] == int(failure is None)
    assert "private backend" not in str(report)


async def test_lost_claim_skips_storage():
    repo, _ = repository()
    repo.claim.return_value = None
    storage = AsyncMock()
    report = await ScanStorageRetentionService(repo, storage).run(dry_run=False)
    assert report["skipped"] == 1
    storage.delete.assert_not_called()


async def test_stale_owner_cannot_report_success():
    repo, _ = repository()
    repo.finalize.return_value = False
    report = await ScanStorageRetentionService(repo, AsyncMock()).run(dry_run=False)
    assert report["skipped"] == 1 and report["purged"] == 0


async def test_real_storage_delete_and_absence(tmp_path):
    storage = FilesystemArtifactStorage(str(tmp_path))
    reference = await storage.store(b"private source", "application/pdf")
    assert (tmp_path / reference).read_bytes() == b"private source"
    assert await storage.delete(reference) is True
    assert not (tmp_path / reference).exists()
    assert await storage.delete(reference) is False


@pytest.mark.parametrize(
    "reference",
    [
        "..",
        ".",
        "../private",
        "a/b",
        "a\\b",
        "C:private",
        "C:\\private",
        "/private",
        "",
        "a" * 129,
    ],
)
async def test_delete_rejects_nonopaque_reference(tmp_path, reference):
    with pytest.raises(ValueError, match="invalid storage reference"):
        await FilesystemArtifactStorage(str(tmp_path)).delete(reference)


async def test_delete_rejects_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"protected")
    root = tmp_path / "objects"
    root.mkdir()
    try:
        (root / "opaque").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(ValueError):
        await FilesystemArtifactStorage(str(root)).delete("opaque")
    assert outside.read_bytes() == b"protected"


@pytest.mark.parametrize("flag,dry_run", [("--dry-run", True), ("--execute", False)])
def test_cli_requires_explicit_mode_without_password_prompt(monkeypatch, flag, dry_run):
    import sys

    from app import cli

    command = AsyncMock()
    monkeypatch.setattr(cli, "scan_retention", command)
    monkeypatch.setattr(sys, "argv", ["app.cli", "scan-retention", flag])
    cli.main()
    command.assert_awaited_once_with(dry_run=dry_run)


def test_cli_rejects_implicit_execute(monkeypatch):
    import sys

    from app import cli

    monkeypatch.setattr(sys, "argv", ["app.cli", "scan-retention"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
