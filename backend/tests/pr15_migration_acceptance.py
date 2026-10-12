"""PR15 disposable PostgreSQL migration roundtrip and fresh schema gates."""

import asyncio
from uuid import uuid4

from tests.pr14_migration_acceptance import command, database


def main():
    for fresh in (False, True):
        name = f"withdrawal_{'fresh' if fresh else 'roundtrip'}_{uuid4().hex}_test"
        url = asyncio.run(database(name, create=True))
        try:
            if fresh:
                command(url, "upgrade", "head")
            else:
                command(url, "upgrade", "20261012_01")
                command(url, "upgrade", "20261012_02")
                command(url, "downgrade", "20261012_01")
                command(url, "upgrade", "20261012_02")
            command(url, "current")
            print("PASS", "fresh head" if fresh else "PR15 roundtrip", flush=True)
        finally:
            asyncio.run(database(name, create=False))


if __name__ == "__main__":
    main()
