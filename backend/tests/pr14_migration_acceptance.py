"""Explicit disposable PostgreSQL PR14 fresh upgrade and downgrade acceptance."""

import asyncio
import os
import subprocess
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


async def database(name, *, create):
    base = make_url(os.environ["TEST_DATABASE_URL"])
    assert base.database.endswith("_test")
    engine = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as db:
            await db.execute(
                text(f'{"CREATE" if create else "DROP"} DATABASE "{name}"')
            )
    finally:
        await engine.dispose()
    return base.set(database=name).render_as_string(hide_password=False)


def command(url, *args):
    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run(["alembic", *args], env=env, check=True)


def main():
    suffix = uuid4().hex
    for fresh in (False, True):
        name = f"retention_{'fresh' if fresh else 'roundtrip'}_{suffix}_test"
        url = asyncio.run(database(name, create=True))
        try:
            if fresh:
                command(url, "upgrade", "head")
            else:
                command(url, "upgrade", "20261011_03")
                command(url, "upgrade", "20261012_01")
                command(url, "downgrade", "20261011_03")
                command(url, "upgrade", "20261012_01")
            command(url, "current")
            print(
                "PASS",
                "fresh upgrade"
                if fresh
                else "20261011_03 -> 20261012_01 -> 20261011_03 -> 20261012_01",
                flush=True,
            )
        finally:
            asyncio.run(database(name, create=False))


if __name__ == "__main__":
    main()
