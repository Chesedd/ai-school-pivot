"""Disposable PostgreSQL matching recovery schema boundary and fresh-head gates."""
import asyncio
from uuid import uuid4

from tests.pr14_migration_acceptance import command, database


def main():
    for fresh in (False, True):
        name = f"paper_recovery_{uuid4().hex}_test"
        url = asyncio.run(database(name, create=True))
        try:
            if fresh:
                command(url, "upgrade", "head")
            else:
                for direction, revision in (("upgrade", "20261012_02"),
                    ("upgrade", "20261013_01"), ("downgrade", "20261012_02"),
                    ("upgrade", "20261013_01")):
                    command(url, direction, revision)
            command(url, "current")
            print("PASS", "fresh base to head" if fresh else "matching recovery roundtrip", flush=True)
        finally:
            asyncio.run(database(name, create=False))


if __name__ == "__main__":
    main()
