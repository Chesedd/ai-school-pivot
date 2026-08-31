"""Explicit operator-only commands (no application-startup side effects)."""
import argparse
import asyncio
import getpass

from app.application.authentication import AuthenticationService
from app.application.user_administration import AdministrationError, UserAdministrationService
from app.db.session import async_session_factory
from app.infrastructure.auth_repository import SQLAlchemyAuthRepository


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
    args = parser.parse_args()
    password = getpass.getpass("New admin password: ")
    try:
        asyncio.run(bootstrap_admin(args.login, args.display_name, password))
    except AdministrationError as exc:
        parser.exit(1, f"Bootstrap refused: {exc.code}\n")


if __name__ == "__main__":
    main()
