"""Multimodal provider boundary: storage bytes and SDK objects stay in this module."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from time import monotonic
from typing import Any

from pydantic import ValidationError

from app.application.authoring import (Cost, FailureCode, ModelRoute, PricingCatalog,
    ProviderFailure, Usage)
from app.application.extraction import StorageReadPort
from app.application.extraction_prompts import (IMAGE_EXTRACT_V1_NAME,
    IMAGE_EXTRACT_V1_SYSTEM, IMAGE_EXTRACT_V1_USER)
from app.application.image_solving_contracts import (ExtractionResultV1, InputArtifactV1,
    SolutionResultV1)
from app.application.extraction_pipeline import SOLVER_SYSTEM, SolverInputV1
from app.infrastructure.authoring_providers import _failure


@dataclass(frozen=True, slots=True)
class ExtractionTelemetry:
    provider_request_id: str
    usage: Usage
    cost: Cost | None
    latency_ms: int


class _ExtractionAdapter:
    provider_id: str

    def __init__(self, client: Any, storage: StorageReadPort,
                 pricing: PricingCatalog | None = None):
        self._client, self._storage, self._pricing = client, storage, pricing
        self.last_telemetry: ExtractionTelemetry | None = None

    async def _content(self, artifact: InputArtifactV1) -> bytes:
        value = await self._storage.read_artifact_bytes(artifact.artifact_id)
        if type(value) is not bytes or not value:
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE)
        return value

    def _finish(self, route: ModelRoute, response: Any, payload: Any, usage: Usage,
                started: float) -> ExtractionResultV1:
        try:
            # Validate from the provider JSON representation.  This preserves the
            # contract's strict Decimal semantics without coercing Python objects.
            result = ExtractionResultV1.model_validate_json(json.dumps(payload))
            request_id = response.id
            if type(request_id) is not str or not request_id:
                raise ValueError
        except (ValidationError, AttributeError, TypeError, ValueError):
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE) from None
        cost = self._pricing.calculate(route, usage) if self._pricing is not None else None
        self.last_telemetry = ExtractionTelemetry(request_id, usage, cost,
            max(0, int((monotonic() - started) * 1000)))
        return result


class OpenAIExtractionAdapter(_ExtractionAdapter):
    """Official Responses API adapter with strict JSON Schema output."""
    provider_id = "openai"

    async def extract(self, artifact: InputArtifactV1, route: ModelRoute) -> ExtractionResultV1:
        started = monotonic()
        try:
            content = base64.b64encode(await self._content(artifact)).decode("ascii")
            if artifact.mime_type == "application/pdf":
                media = {"type": "input_file", "filename": "artifact.pdf",
                    "file_data": f"data:application/pdf;base64,{content}"}
            else:
                media = {"type": "input_image",
                    "image_url": f"data:{artifact.mime_type};base64,{content}"}
            response = await self._client.responses.create(model=route.model_id,
                instructions=IMAGE_EXTRACT_V1_SYSTEM,
                input=[{"role": "user", "content": [media,
                    {"type": "input_text", "text": IMAGE_EXTRACT_V1_USER}]}],
                text={"format": {"type": "json_schema", "name": IMAGE_EXTRACT_V1_NAME,
                    "schema": ExtractionResultV1.model_json_schema(), "strict": True}})
            payload = json.loads(response.output_text)
            details = getattr(response.usage, "input_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) or 0
            ordinary = response.usage.input_tokens - cached
            if ordinary < 0: raise ValueError
            usage = Usage(ordinary, response.usage.output_tokens, cached, 0)
            return self._finish(route, response, payload, usage, started)
        except ProviderFailure: raise
        except (json.JSONDecodeError, ValidationError, AttributeError, TypeError, ValueError):
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE) from None
        except Exception as exc:
            raise _failure(exc) from None


class AnthropicExtractionAdapter(_ExtractionAdapter):
    """Official Messages API adapter; vendor content blocks remain private."""
    provider_id = "anthropic"

    async def extract(self, artifact: InputArtifactV1, route: ModelRoute) -> ExtractionResultV1:
        started = monotonic()
        try:
            content = base64.b64encode(await self._content(artifact)).decode("ascii")
            kind = "document" if artifact.mime_type == "application/pdf" else "image"
            source = {"type": "base64", "media_type": artifact.mime_type, "data": content}
            response = await self._client.messages.create(model=route.model_id,
                system=IMAGE_EXTRACT_V1_SYSTEM, max_tokens=4096,
                messages=[{"role": "user", "content": [
                    {"type": kind, "source": source},
                    {"type": "text", "text": IMAGE_EXTRACT_V1_USER}]}],
                extra_body={"output_config": {"format": {"type": "json_schema",
                    "schema": ExtractionResultV1.model_json_schema()}}})
            raw = "".join(block.text for block in response.content
                if getattr(block, "type", None) == "text")
            payload = json.loads(raw)
            usage = Usage(response.usage.input_tokens, response.usage.output_tokens,
                getattr(response.usage, "cache_read_input_tokens", 0) or 0,
                getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
            return self._finish(route, response, payload, usage, started)
        except ProviderFailure: raise
        except (json.JSONDecodeError, ValidationError, AttributeError, TypeError, ValueError):
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE) from None
        except Exception as exc:
            raise _failure(exc) from None


class RoutedAnthropicExtractor:
    """Bind the configured model route to the provider-neutral extraction port."""
    def __init__(self, adapter: AnthropicExtractionAdapter, route: ModelRoute):
        self.adapter, self.route = adapter, route

    async def extract(self, artifact: InputArtifactV1) -> ExtractionResultV1:
        return await self.adapter.extract(artifact, self.route)


class AnthropicSolverAdapter:
    """Solve extracted task data through the same official Messages client."""
    def __init__(self, client: Any, route: ModelRoute):
        self._client, self._route = client, route

    async def solve(self, value: SolverInputV1) -> SolutionResultV1:
        try:
            response = await self._client.messages.create(model=self._route.model_id,
                system=SOLVER_SYSTEM, max_tokens=4096,
                messages=[{"role": "user", "content": value.model_dump_json()}],
                extra_body={"output_config": {"format": {"type": "json_schema",
                    "schema": SolutionResultV1.model_json_schema()}}})
            raw = "".join(block.text for block in response.content
                if getattr(block, "type", None) == "text")
            return SolutionResultV1.model_validate_json(raw)
        except ProviderFailure: raise
        except (ValidationError, ValueError, TypeError):
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE) from None
        except Exception as exc:
            raise _failure(exc) from None
