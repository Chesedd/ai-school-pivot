"""Explicit operator-only commands (no application-startup side effects)."""
import argparse
import asyncio
import getpass
import json

from app.application.authentication import AuthenticationService
from app.application.scan_storage_retention import ScanStorageRetentionService
from app.application.user_administration import (
    AdministrationError,
    UserAdministrationService,
)
from app.config import get_settings
from app.db.session import async_session_factory
from app.infrastructure.artifact_storage import FilesystemArtifactStorage
from app.infrastructure.auth_repository import SQLAlchemyAuthRepository
from app.infrastructure.scan_orphan_cleanup import sweep_scan_orphans
from app.infrastructure.scan_storage_retention_repository import (
    ScanStorageRetentionRepository,
)


async def scan_retention(*, dry_run=True):
    settings = get_settings()
    service = ScanStorageRetentionService(ScanStorageRetentionRepository(async_session_factory),
        FilesystemArtifactStorage(settings.artifact_storage_path), settings.paper_retention)
    print(json.dumps(await service.run(dry_run=dry_run), sort_keys=True))


async def bootstrap_admin(login: str, display_name: str, password: str) -> None:
    async with async_session_factory() as session:
        repository = SQLAlchemyAuthRepository(session)
        service = UserAdministrationService(repository, AuthenticationService(repository))
        try:
            account = await service.bootstrap(login=login, display_name=display_name, password=password)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    print(f"Admin account created: {account.login}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin")
    bootstrap.add_argument("--login", required=True)
    bootstrap.add_argument("--display-name", required=True)
    retention = commands.add_parser("scan-retention")
    mode = retention.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    orphan = commands.add_parser("scan-orphans")
    orphan_mode = orphan.add_mutually_exclusive_group(required=True)
    orphan_mode.add_argument("--dry-run", action="store_true")
    orphan_mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "scan-orphans":
        print(json.dumps(asyncio.run(sweep_scan_orphans(async_session_factory,
            FilesystemArtifactStorage(get_settings().artifact_storage_path), dry_run=args.dry_run)), sort_keys=True))
        return
    if args.command == "scan-retention":
        asyncio.run(scan_retention(dry_run=args.dry_run))
        return
    password = getpass.getpass("New admin password: ")
    try:
        asyncio.run(bootstrap_admin(args.login, args.display_name, password))
    except AdministrationError as exc:
        parser.exit(1, f"Bootstrap refused: {exc.code}\n")


if __name__ == "__main__":
    main()
