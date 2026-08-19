"""Versioned, transport-neutral LLM boundary for the Checking context.

This module deliberately knows nothing about submissions or grading.  Provider calls are
at-least-once; persistence adapters make each numbered attempt append exactly once.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError

DEFAULT_TIMEOUT_MS = 30_000
MAX_ATTEMPTS = 3
MAX_OUTPUT_TOKENS = 1_000_000
MAX_SEED = 2**63 - 1
_ID = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
ERROR_CODES = frozenset({"timeout", "rate_limited", "transport", "provider_5xx",
    "authentication", "invalid_request", "content_blocked", "invalid_json",
    "schema_invalid", "semantic_invalid", "unknown"})
RETRY_THREE = frozenset({"timeout", "rate_limited", "transport", "provider_5xx"})


class ProviderBoundaryError(ValueError): pass
class RequestConflict(ProviderBoundaryError): pass


def _bounded(value: str, maximum: int, label: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ProviderBoundaryError(f"invalid {label}")
    if identifier and not _ID.fullmatch(value): raise ProviderBoundaryError(f"invalid {label}")
    return value


def canonical_json(value: Any) -> str:
    """Canonical JSON; floats and non-JSON objects are rejected, strings untouched."""
    def visit(item: Any) -> None:
        if item is None or type(item) in (str, bool, int): return
        if isinstance(item, float): raise ProviderBoundaryError("floats are not canonical")
        if isinstance(item, list):
            for child in item: visit(child)
            return
        if isinstance(item, dict):
            if any(type(key) is not str for key in item): raise ProviderBoundaryError("non-string JSON key")
            for child in item.values(): visit(child)
            return
        raise ProviderBoundaryError("unsupported canonical value")
    visit(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def settings_snapshot(settings: Mapping[str, Any]) -> Mapping[str, Any]:
    if type(settings) is not dict: raise ProviderBoundaryError("settings must be a plain object")
    if set(settings) - {"temperature", "seed", "max_output_tokens"}:
        raise ProviderBoundaryError("unsupported setting")
    result: dict[str, Any] = {}
    for key, value in settings.items():
        if key == "temperature":
            if type(value) is not str or "e" in value.lower(): raise ProviderBoundaryError("invalid temperature")
            try: number = Decimal(value)
            except InvalidOperation: raise ProviderBoundaryError("invalid temperature") from None
            if not number.is_finite() or number < 0 or number > 2 or format(number, "f") != value:
                raise ProviderBoundaryError("invalid temperature")
        elif key == "seed":
            if type(value) is not int or not -MAX_SEED <= value <= MAX_SEED: raise ProviderBoundaryError("invalid seed")
        else:
            if type(value) is not int or not 1 <= value <= MAX_OUTPUT_TOKENS: raise ProviderBoundaryError("invalid max output tokens")
        result[key] = value
    return MappingProxyType(result)


@dataclass(frozen=True)
class ProviderMessage:
    role: str
    content: str
    def __post_init__(self):
        if self.role not in {"system", "user"}: raise ProviderBoundaryError("invalid message role")
        if not isinstance(self.content, str) or not self.content or len(self.content) > 60_000:
            raise ProviderBoundaryError("invalid message content")


@dataclass(frozen=True)
class PromptSpec:
    stable_name: str; semantic_version: str; template_text: str; output_schema_version: str
    def __post_init__(self):
        _bounded(self.stable_name, 120, "prompt name", identifier=True)
        _bounded(self.semantic_version, 64, "prompt version")
        _bounded(self.output_schema_version, 64, "schema version")
        if not isinstance(self.template_text, str) or not self.template_text: raise ProviderBoundaryError("invalid template")
    @property
    def template_hash(self) -> str:
        return hashlib.sha256(self.template_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderRequest:
    provider_id: str; model_id: str; settings: Mapping[str, Any]; prompt: PromptSpec
    schema_document: Mapping[str, Any]; messages: tuple[ProviderMessage, ...]
    request_fingerprint: str; correlation_token: str; timeout_ms: int = DEFAULT_TIMEOUT_MS
    def __post_init__(self):
        _bounded(self.provider_id, 128, "provider id", identifier=True); _bounded(self.model_id, 128, "model id", identifier=True)
        if type(self.timeout_ms) is not int or not 1 <= self.timeout_ms <= DEFAULT_TIMEOUT_MS: raise ProviderBoundaryError("invalid timeout")
        detached = settings_snapshot(dict(self.settings) if isinstance(self.settings, MappingProxyType) else self.settings)
        object.__setattr__(self, "settings", detached)
        if type(self.schema_document) is not dict: raise ProviderBoundaryError("schema must be a plain object")
        schema = json.loads(canonical_json(self.schema_document)); object.__setattr__(self, "schema_document", MappingProxyType(schema))
        if type(self.messages) is not tuple or not self.messages: raise ProviderBoundaryError("messages must be a non-empty tuple")
        if not re.fullmatch(r"[0-9a-f]{64}", self.request_fingerprint): raise ProviderBoundaryError("invalid fingerprint")
        if self.correlation_token != "sha256:" + self.request_fingerprint: raise ProviderBoundaryError("invalid correlation token")


def build_request(*, provider_id: str, model_id: str, prompt: PromptSpec,
                  contract: "StructuredOutputContract", messages: tuple[ProviderMessage, ...],
                  settings: dict[str, Any], timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ProviderRequest:
    snapshot = settings_snapshot(settings)
    schema = contract.json_schema()
    body = {"provider_id": provider_id, "model_id": model_id, "timeout_ms": timeout_ms,
        "settings": dict(snapshot), "prompt_name": prompt.stable_name,
        "prompt_semantic_version": prompt.semantic_version, "prompt_template_hash": prompt.template_hash,
        "output_schema_version": prompt.output_schema_version, "schema": schema,
        "messages": [{"role": m.role, "content": m.content} for m in messages]}
    fingerprint = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return ProviderRequest(provider_id, model_id, snapshot, prompt, schema, messages,
                           fingerprint, "sha256:" + fingerprint, timeout_ms)


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int; output_tokens: int; cached_tokens: int = 0
    def __post_init__(self):
        if any(type(x) is not int or x < 0 or x > 2**31 - 1 for x in (self.input_tokens, self.output_tokens, self.cached_tokens)):
            raise ProviderBoundaryError("invalid usage")


@dataclass(frozen=True)
class ProviderResponse:
    provider_request_id: str; raw_output: str; parsed_candidate: Any | None = None
    usage: ProviderUsage | None = None; latency_ms: int = 0; finish_reason: str = "stop"
    metadata: tuple[tuple[str, str], ...] = ()
    def __post_init__(self):
        _bounded(self.provider_request_id, 256, "provider request id")
        if not isinstance(self.raw_output, str) or len(self.raw_output) > 1_000_000: raise ProviderBoundaryError("invalid raw output")
        if type(self.latency_ms) is not int or self.latency_ms < 0: raise ProviderBoundaryError("invalid latency")
        if self.finish_reason not in {"stop", "length", "content_filter", "tool", "unknown"}: raise ProviderBoundaryError("invalid finish reason")
        if type(self.metadata) is not tuple or len(self.metadata) > 16: raise ProviderBoundaryError("invalid metadata")
        allowed = {"region", "service_tier", "api_version"}
        for pair in self.metadata:
            if type(pair) is not tuple or len(pair) != 2 or pair[0] not in allowed: raise ProviderBoundaryError("invalid metadata")
            _bounded(pair[1], 128, "metadata value")


class ProviderFailure(Exception):
    def __init__(self, code: str, detail: str = "provider request failed"):
        if code not in ERROR_CODES: raise ProviderBoundaryError("unsupported provider error")
        self.code = code; self.detail = _bounded(detail, 200, "safe error detail")
        super().__init__(code)


@runtime_checkable
class LLMProvider(Protocol):
    async def evaluate(self, request: ProviderRequest) -> ProviderResponse: ...


class ContractProbeOutput(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: str
    acknowledged: StrictBool


@runtime_checkable
class StructuredOutputContract(Protocol):
    @property
    def schema_version(self) -> str: ...
    def json_schema(self) -> dict[str, Any]: ...
    def validate(self, candidate: dict[str, Any]) -> Mapping[str, Any]: ...


class ContractProbe:
    schema_version = "provider-contract-probe.v1"
    def json_schema(self) -> dict[str, Any]:
        schema = ContractProbeOutput.model_json_schema()
        schema["properties"]["schema_version"] = {"const": self.schema_version, "title": "Schema Version", "type": "string"}
        schema["additionalProperties"] = False
        return schema
    def validate(self, candidate: dict[str, Any]) -> Mapping[str, Any]:
        value = ContractProbeOutput.model_validate(candidate)
        if value.schema_version != self.schema_version: raise ValueError("schema version mismatch")
        return MappingProxyType(value.model_dump(mode="json"))


def validate_response(response: ProviderResponse, contract: StructuredOutputContract) -> Mapping[str, Any]:
    try: parsed = json.loads(response.raw_output)
    except (json.JSONDecodeError, UnicodeError) as exc: raise ProviderFailure("invalid_json", "response was not valid JSON") from None
    if type(parsed) is not dict: raise ProviderFailure("schema_invalid", "response was not one JSON object")
    if response.parsed_candidate is not None:
        try: same = canonical_json(response.parsed_candidate) == canonical_json(parsed)
        except ProviderBoundaryError: same = False
        if not same: raise ProviderFailure("schema_invalid", "parsed candidate mismatch")
    try: candidate = contract.validate(parsed)
    except (ValidationError, ValueError, TypeError): raise ProviderFailure("schema_invalid", "response did not match schema") from None
    return MappingProxyType({"output_schema_version": contract.schema_version,
        "candidate": dict(candidate), "finish": {"reason": response.finish_reason,
        "metadata": dict(response.metadata)}})


@dataclass(frozen=True)
class Pricing:
    currency: str; pricing_version: str; pricing_source: str
    input_token_rate: Decimal; output_token_rate: Decimal; cached_token_rate: Decimal
    def __post_init__(self):
        if not re.fullmatch(r"[A-Z]{3}", self.currency): raise ProviderBoundaryError("invalid currency")
        _bounded(self.pricing_version, 64, "pricing version"); _bounded(self.pricing_source, 128, "pricing source")
        for rate in (self.input_token_rate, self.output_token_rate, self.cached_token_rate):
            if type(rate) is not Decimal or not rate.is_finite() or rate < 0: raise ProviderBoundaryError("invalid token rate")
    def cost(self, usage: ProviderUsage) -> Decimal:
        total = (Decimal(usage.input_tokens) * self.input_token_rate +
                 Decimal(usage.output_tokens) * self.output_token_rate +
                 Decimal(usage.cached_tokens) * self.cached_token_rate)
        return total.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def retry_allowed(code: str, completed_attempts: int) -> bool:
    return completed_attempts < (3 if code in RETRY_THREE else 2 if code == "invalid_json" else 1)


async def call_with_timeout(provider: LLMProvider, request: ProviderRequest) -> ProviderResponse:
    try: return await asyncio.wait_for(provider.evaluate(request), request.timeout_ms / 1000)
    except asyncio.TimeoutError: raise ProviderFailure("timeout", "provider request timed out") from None


async def run_with_retries(provider: LLMProvider, request: ProviderRequest, contract: StructuredOutputContract,
                           *, sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
                           jitter: Callable[[], float] = random.random,
                           monotonic: Callable[[], float] | None = None) -> Mapping[str, Any]:
    """Database-free policy primitive used by orchestration and deterministic tests."""
    del monotonic  # injected clock is part of the stable seam; adapters own latency values.
    attempt = 0
    while True:
        attempt += 1
        try: return validate_response(await call_with_timeout(provider, request), contract)
        except ProviderFailure as failure:
            if not retry_allowed(failure.code, attempt): raise
            sample = jitter()
            if isinstance(sample, bool) or not isinstance(sample, (int, float)) or not math.isfinite(sample) or not 0 <= sample <= 1:
                raise ProviderBoundaryError("invalid jitter source")
            await sleeper(min(30.0, 2 ** (attempt - 1)) * sample)


@dataclass(frozen=True)
class AttemptState:
    """Privacy-safe store projection; raw output never returns through this service."""
    attempt_id: str
    attempt_no: int
    status: str
    request_fingerprint: str
    validated_output: Mapping[str, Any] | None = None
    error_code: str | None = None


@runtime_checkable
class ProviderAttemptStore(Protocol):
    async def replay_or_claim(self, request: ProviderRequest, prompt: PromptSpec,
                              maximum_attempts: int) -> AttemptState: ...
    async def finalize(self, attempt: AttemptState, *, status: str,
                       response: ProviderResponse | None, validated_output: Mapping[str, Any] | None,
                       error_code: str | None, pricing: Pricing | None) -> AttemptState: ...


@dataclass(frozen=True)
class ExecutionOutcome:
    state: str
    attempt_no: int
    validated_output: Mapping[str, Any] | None = None
    error_code: str | None = None


class ProviderExecutionService:
    """Own retries while a store owns short claim/finalize transactions."""
    def __init__(self, store: ProviderAttemptStore, provider: LLMProvider, *,
                 sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 jitter: Callable[[], float] = random.random,
                 monotonic: Callable[[], float] = __import__("time").monotonic):
        self.store, self.provider = store, provider
        self.sleeper, self.jitter, self.monotonic = sleeper, jitter, monotonic

    async def execute(self, request: ProviderRequest, prompt: PromptSpec,
                      contract: StructuredOutputContract, pricing: Pricing | None = None) -> ExecutionOutcome:
        while True:
            attempt = await self.store.replay_or_claim(request, prompt, MAX_ATTEMPTS)
            if attempt.request_fingerprint != request.request_fingerprint: raise RequestConflict("request fingerprint conflict")
            if attempt.status == "succeeded":
                return ExecutionOutcome("succeeded", attempt.attempt_no, attempt.validated_output)
            if attempt.status == "running" and attempt.attempt_id.startswith("existing:"):
                return ExecutionOutcome("in_progress", attempt.attempt_no)
            if attempt.status in {"failed", "invalid"}:
                code = attempt.error_code or "unknown"
                if not retry_allowed(code, attempt.attempt_no): return ExecutionOutcome(attempt.status, attempt.attempt_no, error_code=code)
                await self._backoff(attempt.attempt_no)
                continue
            response: ProviderResponse | None = None
            try:
                response = await call_with_timeout(self.provider, request)
                envelope = validate_response(response, contract)
                terminal = await self.store.finalize(attempt, status="succeeded", response=response,
                    validated_output=envelope, error_code=None, pricing=pricing)
                return ExecutionOutcome("succeeded", terminal.attempt_no, terminal.validated_output)
            except ProviderFailure as failure:
                invalid = failure.code in {"invalid_json", "schema_invalid", "semantic_invalid"}
                terminal = await self.store.finalize(attempt, status="invalid" if invalid else "failed",
                    response=response, validated_output=None, error_code=failure.code, pricing=pricing)
                if not retry_allowed(failure.code, terminal.attempt_no):
                    return ExecutionOutcome(terminal.status, terminal.attempt_no, error_code=failure.code)
                await self._backoff(terminal.attempt_no)

    async def _backoff(self, attempt_no: int) -> None:
        sample = self.jitter()
        if isinstance(sample, bool) or not isinstance(sample, (int, float)) or not math.isfinite(sample) or not 0 <= sample <= 1:
            raise ProviderBoundaryError("invalid jitter source")
        await self.sleeper(min(30.0, 2 ** (attempt_no - 1)) * sample)
