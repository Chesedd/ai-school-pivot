"""Regression checks for HTTP workflows removed during the pre-4A cleanup."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unit:unit@localhost/unit")

from fastapi.routing import APIRoute

from app.main import app


def test_import_endpoints_are_absent_from_routes_and_openapi() -> None:
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/api/content-bank/imports/preview" not in paths
    assert "/api/content-bank/imports/commit" not in paths
    assert all("/imports/" not in path for path in app.openapi()["paths"])


def test_user_managed_version_creation_is_absent_from_routes_and_openapi() -> None:
    path = "/api/content-bank/tasks/{task_id}/versions"
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert path not in paths
    assert path not in app.openapi()["paths"]
