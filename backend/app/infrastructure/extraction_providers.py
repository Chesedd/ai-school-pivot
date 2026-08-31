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


def _tool_input(response: Any, name: str) -> dict[str, Any]:
    """Return the sole forced-tool payload, ignoring non-tool content blocks."""
    blocks = [block for block in response.content
        if getattr(block, "type", None) == "tool_use"]
    if len(blocks) != 1 or getattr(blocks[0], "name", None) != name:
        raise ValueError
    payload = getattr(blocks[0], "input")
    if type(payload) is not dict:
        raise TypeError
    return payload


def _normalize_tool_strings(
    payload: dict[str, Any], fields: tuple[str, ...],
) -> dict[str, Any]:
    """Copy a tool payload and trim only named string values at its boundary."""
    normalized = dict(payload)
    for field in fields:
        value = normalized.get(field)
        if type(value) is str:
            normalized[field] = value.strip()
    return normalized


def _normalize_string_list(payload: dict[str, Any], field: str) -> None:
    """Trim a list only when its complete shape is known to be strings."""
    value = payload.get(field)
    if type(value) is list and all(type(item) is str for item in value):
        payload[field] = [item.strip() for item in value]


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
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(map(str, error['loc']))}: {error['type']}"
                for error in exc.errors(include_url=False, include_input=False)
            )
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE,
                f"ValidationError: {details}") from None
        except (AttributeError, TypeError, ValueError) as exc:
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE,
                type(exc).__name__) from None
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
                tools=[{"name": "record_extraction", "description": (
                    "Record only the faithful extraction of the task visible in the "
                    "artifact. Do not solve the task."),
                    "input_schema": ExtractionResultV1.model_json_schema()}],
                tool_choice={"type": "tool", "name": "record_extraction"})
            payload = _tool_input(response, "record_extraction")
            payload = _normalize_tool_strings(payload, ("extracted_text",
                "structured_statement", "detected_task_type", "detected_answer_format"))
            _normalize_string_list(payload, "choices")
            _normalize_string_list(payload, "ocr_issues")
            metadata = payload.get("metadata")
            if type(metadata) is str:
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    raise ProviderFailure(FailureCode.MALFORMED_RESPONSE,
                        "metadata: malformed JSON string") from None
                if type(metadata) is not dict:
                    raise ProviderFailure(FailureCode.MALFORMED_RESPONSE,
                        "metadata: decoded value is not an object")
            if type(metadata) is dict:
                metadata = _normalize_tool_strings(metadata, ("title", "subject", "topic",
                    "subtopic", "task_type", "answer_format"))
                _normalize_string_list(metadata, "skills")
                _normalize_string_list(metadata, "tags")
                payload["metadata"] = metadata
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

    @property
    def provider_id(self) -> str:
        return self.adapter.provider_id

    @property
    def model_id(self) -> str:
        return self.route.model_id

    @property
    def last_telemetry(self) -> ExtractionTelemetry | None:
        return self.adapter.last_telemetry

    async def extract(self, artifact: InputArtifactV1) -> ExtractionResultV1:
        return await self.adapter.extract(artifact, self.route)


class AnthropicSolverAdapter:
    """Solve extracted task data through the same official Messages client."""
    def __init__(self, client: Any, route: ModelRoute):
        self._client, self._route = client, route
        self.last_telemetry: ExtractionTelemetry | None = None

    @property
    def provider_id(self) -> str:
        return self._route.provider_id

    @property
    def model_id(self) -> str:
        return self._route.model_id

    @property
    def route(self) -> ModelRoute:
        return self._route

    async def solve(self, value: SolverInputV1) -> SolutionResultV1:
        started = monotonic()
        try:
            response = await self._client.messages.create(model=self._route.model_id,
                system=SOLVER_SYSTEM, max_tokens=4096,
                messages=[{"role": "user", "content": value.model_dump_json()}],
                tools=[{"name": "record_solution", "description": (
                    "Record the final solution for the extracted task according to "
                    "the required solver contract."),
                    "input_schema": SolutionResultV1.model_json_schema()}],
                tool_choice={"type": "tool", "name": "record_solution"})
            payload = _tool_input(response, "record_solution")
            normalized_payload = _normalize_tool_strings(payload,
                ("status", "reasoning_summary", "final_answer"))
            # Confirmed AIPRIME compatibility alias.  No other status is widened.
            if normalized_payload.get("status") == "success":
                normalized_payload["status"] = "solved"
            result = SolutionResultV1.model_validate_json(json.dumps(
                normalized_payload, ensure_ascii=False))
            usage = Usage(response.usage.input_tokens, response.usage.output_tokens,
                getattr(response.usage, "cache_read_input_tokens", 0) or 0,
                getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
            request_id = response.id
            if type(request_id) is not str or not request_id:
                raise ValueError
            self.last_telemetry = ExtractionTelemetry(request_id, usage, None,
                max(0, int((monotonic() - started) * 1000)))
            return result
        except ProviderFailure: raise
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(map(str, error['loc']))}: {error['type']}"
                for error in exc.errors(include_url=False, include_input=False)
            )
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE,
                f"ValidationError: {details}") from None
        except (AttributeError, ValueError, TypeError) as exc:
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE,
                type(exc).__name__) from None
        except Exception as exc:
            raise _failure(exc) from None
