"""Local acceptance runner: create dedicated disposable PR11 databases only."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


async def main():
    root = Path(__file__).resolve().parents[2]
    cfg = dict(
        line.split("=", 1)
        for line in (root / ".env").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    base = make_url(cfg["DATABASE_URL"]).set(
        host="localhost", port=int(cfg.get("POSTGRES_PORT", "5432"))
    )
    name = os.getenv("PR11_TEST_DB", "paper_remediation_pr11_test")
    if name not in {
        "paper_remediation_pr11_test",
        "paper_remediation_pr11_migration_test",
    }:
        raise RuntimeError("Only dedicated PR11 databases allowed")
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as db:
            if not await db.scalar(
                text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}
            ):
                await db.execute(text(f'CREATE DATABASE "{name}"'))
            # Avoid JIT compilation of asyncpg's catalog introspection on every
            # isolated test connection. This setting is confined to these test DBs.
            await db.execute(text(f'ALTER DATABASE "{name}" SET jit = off'))
    finally:
        await admin.dispose()
    url = base.set(database=name).render_as_string(hide_password=False)
    env = {**os.environ, "TEST_DATABASE_URL": url, "DATABASE_URL": url}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    if os.name == "nt":
        env["PYTEST_PLUGINS"] = ",".join(
            filter(None, [env.get("PYTEST_PLUGINS"), "tests.pr11_windows_pytest_ids"])
        )
    cmd = [sys.executable, "-m", *sys.argv[1:]]
    if sys.argv[1:] == ["matrix"]:
        paper = "tests/integration/test_paper_remediation_database.py"
        publication = "tests/integration/test_paper_result_publication_database.py"
        batch = "tests/integration/test_paper_batch_orchestration_database.py"
        digital = [
            f"tests/integration/test_remediation{suffix}_database.py"
            for suffix in ("", "_execution", "_checking", "_results")
        ]
        units = [
            f"tests/unit/test_remediation_{suffix}.py"
            for suffix in ("checking", "execution", "results", "presentation_security")
        ]
        gates = [
            ("existing-remediation", digital + units),
            ("pr9-pr11", [publication, paper]),
            ("pr11-pr9", [paper, publication]),
            ("pr10-pr11", [batch, paper]),
            ("pr11-pr10", [paper, batch]),
            ("digital-pr11", digital + [paper]),
            ("pr11-digital", [paper] + digital),
            (
                "relevant-integration",
                [
                    "tests/integration",
                    "-k",
                    "checking or scan or publication or batch or remediation",
                ],
            ),
        ]
        failures = 0
        for label, args in gates:
            print(f"Starting {label}", flush=True)
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    *args,
                    "--tb=short",
                    f"--junitxml=pr11-{label}.xml",
                ],
                cwd=root / "backend",
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            output = result.stdout + result.stderr
            (root / "backend" / f"pr11-{label}.log").write_text(
                output, encoding="utf-8"
            )
            print(
                f"Finished {label}: exit {result.returncode}\n"
                + "\n".join(output.splitlines()[-6:]),
                flush=True,
            )
            failures += result.returncode != 0
        return int(bool(failures))
    result = await asyncio.to_thread(
        subprocess.run, cmd, cwd=root / "backend", env=env, check=False
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
