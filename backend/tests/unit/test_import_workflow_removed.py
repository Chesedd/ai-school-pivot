"""Regression checks for removal of the legacy task-import HTTP surface."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unit:unit@localhost/unit")
os.environ.setdefault("CONTENT_BANK_DEV_ACTOR_ID", "00000000-0000-4000-8000-000000000001")
os.environ.setdefault("ASSESSMENT_DEV_STUDENT_ID", "00000000-0000-4000-8000-000000000002")

from fastapi.routing import APIRoute

from app.main import app


def test_import_endpoints_are_absent_from_routes_and_openapi() -> None:
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/api/content-bank/imports/preview" not in paths
    assert "/api/content-bank/imports/commit" not in paths
    assert all("/imports/" not in path for path in app.openapi()["paths"])
