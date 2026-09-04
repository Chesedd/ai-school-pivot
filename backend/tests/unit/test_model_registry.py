"""Regression coverage for standalone persistence model registration."""
import os
import subprocess
import sys
from pathlib import Path


def test_seed_import_can_register_complete_model_graph_in_fresh_interpreter():
    backend_root = Path(__file__).parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from app.infrastructure.model_registry import register_all_models
from app.infrastructure.models import Base
from app.tools.seed_school_catalog import seed_catalog
from sqlalchemy.orm import configure_mappers

register_all_models()
register_all_models()
configure_mappers()

required = {
    "users",
    "subjects",
    "grades",
    "topics",
    "subtopics",
    "skills",
    "students",
    "check_runs",
    "authoring_sessions",
    "image_solving_sessions",
}
missing = required - set(Base.metadata.tables)
assert not missing, missing
assert callable(seed_catalog)
""",
        ],
        cwd=backend_root,
        env={
            **os.environ,
            "DATABASE_URL": "postgresql+asyncpg://registry:registry@localhost/registry",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
