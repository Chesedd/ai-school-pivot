"""Configuration and dependency wiring for the local Image Solving runtime."""
import logging
import os
import sys
from types import SimpleNamespace as NS

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unit:unit@localhost/unit")
os.environ.setdefault("CONTENT_BANK_DEV_ACTOR_ID", "00000000-0000-4000-8000-000000000001")
os.environ.setdefault("ASSESSMENT_DEV_STUDENT_ID", "00000000-0000-4000-8000-000000000002")

from app.application.authoring import FailureCode, ProviderFailure
from app.config import Settings
from app.infrastructure.extraction_providers import (AnthropicSolverAdapter,
    RoutedAnthropicExtractor)
from app.presentation.image_solving_routes import image_solving_service, metadata_service


def settings(**overrides):
    values = dict(database_url="postgresql+asyncpg://unit:unit@localhost/unit",
        content_bank_dev_actor_id="00000000-0000-4000-8000-000000000001",
        assessment_dev_student_id="00000000-0000-4000-8000-000000000002")
    values.update(overrides)
    return Settings(**values)


def test_image_solving_uses_confirmed_anthropic_model_by_default():
    assert settings().image_solving_anthropic_model == "claude-sonnet-4-6"


def test_metadata_composition_keeps_concrete_database_session():
    db = NS()
    recommendations = metadata_service(db, settings())
    assert recommendations.sessions.repository.db is db
    assert recommendations.repository.db is db


def test_gateway_token_builds_real_runtime_and_passes_base_url(monkeypatch):
    captured = {}

    def client(**options):
        captured.update(options)
        return NS(messages=NS())

    monkeypatch.setitem(sys.modules, "anthropic", NS(AsyncAnthropic=client))
    service = image_solving_service(NS(), settings(anthropic_base_url="https://aiprimetech.io",
        anthropic_auth_token="gateway-secret"))

    assert isinstance(service.flow.extractor, RoutedAnthropicExtractor)
    assert isinstance(service.flow.solver, AnthropicSolverAdapter)
    assert captured == {"base_url": "https://aiprimetech.io",
        "auth_token": "gateway-secret"}


def test_api_key_compatibility_uses_sdk_api_key(monkeypatch):
    captured = {}
    monkeypatch.setitem(sys.modules, "anthropic", NS(AsyncAnthropic=lambda **options:
        captured.update(options) or NS(messages=NS())))
    image_solving_service(NS(), settings(anthropic_api_key="official-secret"))
    assert captured == {"base_url": None, "api_key": "official-secret"}


@pytest.mark.asyncio
async def test_missing_credentials_retains_provider_unavailable():
    service = image_solving_service(NS(), settings())
    with pytest.raises(ProviderFailure) as error:
        await service.flow.integrity.sha256(None)
    assert error.value.code is FailureCode.PROVIDER_UNAVAILABLE


def test_credentials_are_redacted_and_not_logged(caplog):
    configured = settings(anthropic_auth_token="never-print-this",
        anthropic_api_key="also-never-print-this")
    with caplog.at_level(logging.DEBUG):
        logging.getLogger(__name__).debug("settings=%r", configured)
    public = configured.model_dump(mode="json")
    combined = repr(configured) + caplog.text + repr(public)
    assert "never-print-this" not in combined
    assert "also-never-print-this" not in combined
    assert public["anthropic_auth_token"] == "**********"
