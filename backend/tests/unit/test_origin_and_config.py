from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from app.config import Settings
from app.security.origin import TrustedOriginPolicy


def request(method, origin=None):
    headers = [] if origin is None else [(b"origin", origin.encode())]
    return Request({"type": "http", "method": method, "path": "/", "headers": headers})


def test_origin_policy_accepts_trusted_and_missing_origin_and_ignores_safe_methods():
    policy = TrustedOriginPolicy(("http://localhost:5173",))
    policy.enforce(request("POST", "http://localhost:5173"))
    policy.enforce(request("POST"))  # deterministic non-browser client policy
    policy.enforce(request("GET", "https://foreign.example"))


def test_origin_policy_rejects_explicit_foreign_origin():
    with pytest.raises(Exception) as caught:
        TrustedOriginPolicy(("http://localhost:5173",)).enforce(request("POST", "https://foreign.example"))
    assert caught.value.status_code == 403


def test_wildcard_credentialed_cors_is_invalid():
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+asyncpg://u:p@localhost/db",
            content_bank_dev_actor_id=uuid4(),
            assessment_dev_student_id=uuid4(),
            cors_origins="*",
        )


def test_credentialed_cors_allows_only_configured_origin():
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET"],
    )
    app.get("/")(lambda: {"ok": True})
    client = TestClient(app)
    trusted = client.get("/", headers={"Origin": "http://localhost:5173"})
    assert trusted.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert trusted.headers["access-control-allow-credentials"] == "true"
    foreign = client.get("/", headers={"Origin": "https://foreign.example"})
    assert "access-control-allow-origin" not in foreign.headers
