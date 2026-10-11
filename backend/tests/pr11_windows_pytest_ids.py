"""Keep Windows PYTEST_CURRENT_TEST within OS bounds, without changing cases."""

import hashlib
import os
import sys
from pathlib import Path


def pytest_sessionstart(session):
    # The runner invokes the venv's Python directly rather than activating it.
    # Existing migration tests also launch the installed `alembic` entry point.
    os.environ["PATH"] = (
        str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    )


def pytest_collection_modifyitems(items):
    if os.name != "nt":
        return
    for item in items:
        if len(item.nodeid) > 1024:
            digest = hashlib.sha256(item.nodeid.encode()).hexdigest()[:24]
            item._nodeid = item.nodeid.split("[", 1)[0] + f"[{digest}]"
