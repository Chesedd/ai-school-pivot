"""Content Bank-owned provider execution foundation for AI authoring Phase 4A.1."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, ValidationError, field_validator

MAX_TEXT = 2_000
MAX_SOURCE = 30_000
MAX_ITEMS = 32
MAX_TOKENS = 10_000_000
MAX_TIMEOUT_MS = 120_000
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AuthoringError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class AuthoringConflict(AuthoringError):
    def __init__(self): super().__init__("request_conflict")


def _identifier(value: str, *, maximum: int = 128) -> str:
    if not value or len(value) > maximum or value != value.strip() or not ID_PATTERN.fullmatch(value):
        raise AuthoringError("unsupported_value")
    return value


def freeze_json(value: Any) -> Any:
    if value is None or type(value) in (str, bool, int): return value
    if isinstance(value, float): raise AuthoringError("invalid_request")
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value): raise AuthoringError("invalid_request")
        return MappingProxyType({k: freeze_json(v) for k, v in value.items()})
    if type(value) in (list, tuple): return tuple(freeze_json(v) for v in value)
    raise AuthoringError("invalid_request")


def thaw_json(value: Any) -> Any:
    if value is None or type(value) in (str, bool, int): return value
    if isinstance(value, Mapping): return {k: thaw_json(v) for k, v in value.items()}
    if type(value) in (list, tuple): return [thaw_json(v) for v in value]
    raise AuthoringError("invalid_request")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(thaw_json(freeze_json(value)), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


class TaskType(StrEnum):
    TEST="test"; CALCULATION="calculation"; PROBLEM="problem"; OPEN_QUESTION="open_question"; ESSAY="essay"


class AnswerFormat(StrEnum):
    SINGLE_CHOICE="single_choice"; MULTIPLE_CHOICE="multiple_choice"; SHORT_TEXT="short_text"
    NUMBER="number"; EXPRESSION="expression"; LONG_TEXT="long_text"


class AuthoringRequestV1(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: StrictStr
    task_goal: StrictStr
    subject: StrictStr
    grade: StrictStr
    topic: StrictStr
    subtopic: StrictStr | None = None
    task_type: Literal["test","calculation","problem","open_question","essay"]
    answer_format: Literal["single_choice","multiple_choice","short_text","number","expression","long_text"]
    difficulty: StrictInt
    skills: tuple[StrictStr, ...]
    pedagogical_constraints: tuple[StrictStr, ...] = ()
    source_text: StrictStr | None = None
    language: StrictStr | None = None
    policy_version: StrictStr

    @field_validator("schema_version")
    @classmethod
    def schema(cls, value: str) -> str:
        if value != "authoring-request.v1": raise ValueError("unsupported_value")
        return value

    @field_validator("subject", "grade", "topic", "subtopic", "policy_version", "language")
    @classmethod
    def identifiers(cls, value: str | None) -> str | None:
        return None if value is None else _identifier(value)

    @field_validator("task_goal")
    @classmethod
    def goal(cls, value: str) -> str:
        if not value or value != value.strip(): raise ValueError("invalid_request")
        if len(value) > MAX_TEXT: raise ValueError("value_too_large")
        return value

    @field_validator("source_text")
    @classmethod
    def source(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()): raise ValueError("invalid_request")
        if value is not None and len(value) > MAX_SOURCE: raise ValueError("value_too_large")
        return value

    @field_validator("difficulty")
    @classmethod
    def difficulty_range(cls, value: int) -> int:
        if not 1 <= value <= 100: raise ValueError("unsupported_value")
        return value

    @field_validator("skills", "pedagogical_constraints")
    @classmethod
    def arrays(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_ITEMS: raise ValueError("value_too_large")
        if any(not item or item != item.strip() or len(item) > 128 for item in value): raise ValueError("invalid_request")
        if len(set(value)) != len(value): raise ValueError("invalid_request")
        return value

    def canonical_bytes(self) -> bytes: return canonical_json_bytes(self.model_dump(mode="json"))
    @property
    def fingerprint(self) -> str: return hashlib.sha256(self.canonical_bytes()).hexdigest()


def validate_authoring_request(data: Mapping[str, Any]) -> AuthoringRequestV1:
    """Expose only the bounded application error vocabulary to callers."""
    try: return AuthoringRequestV1.model_validate(data)
    except ValidationError as error:
        messages=" ".join(str(item.get("ctx",{}).get("error","")) for item in error.errors())
        code=next((candidate for candidate in ("value_too_large","unsupported_value","catalog_reference_not_allowed") if candidate in messages),"invalid_request")
        raise AuthoringError(code) from None


@dataclass(frozen=True)
class FrozenCatalogContext:
    subject: str; grade: str; topic: str; subtopic: str | None; skills: tuple[str, ...]
    def __post_init__(self):
        for value in (self.subject, self.grade, self.topic): _identifier(value)
        if self.subtopic is not None: _identifier(self.subtopic)
        if not self.skills or len(self.skills) > MAX_ITEMS: raise AuthoringError("catalog_reference_not_allowed")
        for skill in self.skills: _identifier(skill)
        if len(set(self.skills)) != len(self.skills): raise AuthoringError("invalid_request")
    def validate_request(self, request: AuthoringRequestV1) -> None:
        if (request.subject, request.grade, request.topic, request.subtopic) != (self.subject, self.grade, self.topic, self.subtopic) or any(s not in self.skills for s in request.skills):
            raise AuthoringError("catalog_reference_not_allowed")
    def as_json(self) -> dict[str, Any]:
        return {"subject":self.subject,"grade":self.grade,"topic":self.topic,"subtopic":self.subtopic,"skills":list(self.skills)}


class AuthoringRole(StrEnum): GENERATOR="generator"; SOLVER="solver"


@dataclass(frozen=True)
class ModelRoute:
    """An explicit, provider-owned model selection (model ids are opaque to core)."""
    provider_id: str; model_id: str
    def __post_init__(self): _identifier(self.provider_id); _identifier(self.model_id)


@dataclass(frozen=True)
class ProviderCapabilities:
    structured_output: bool = True
    usage_reporting: bool = True
    cache_usage_reporting: bool = False


@dataclass(frozen=True)
class PromptSpecification:
    stable_name: str; role: AuthoringRole; semantic_version: str; template_version: str
    template_hash: str; output_schema_version: str; policy_version: str
    def __post_init__(self):
        for value in (self.stable_name,self.semantic_version,self.template_version,self.output_schema_version,self.policy_version): _identifier(value)
        if not HASH_PATTERN.fullmatch(self.template_hash): raise AuthoringError("invalid_request")


class PromptRegistry:
    def __init__(self, specifications: tuple[PromptSpecification, ...]):
        keys=[(s.stable_name,s.semantic_version) for s in specifications]
        if len(keys) != len(set(keys)): raise AuthoringError("invalid_request")
        self._items=MappingProxyType(dict(zip(keys,specifications,strict=True)))
    def get(self, name: str, version: str) -> PromptSpecification: return self._items[(name,version)]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    def __post_init__(self):
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 5: raise AuthoringError("unsupported_value")


class FailureCode(StrEnum):
    TIMEOUT="timeout"; RATE_LIMIT="rate_limit"; TRANSIENT_TRANSPORT="transient_transport"; PROVIDER_5XX="provider_5xx"
    AUTHENTICATION="authentication"; INVALID_PROVIDER_REQUEST="invalid_provider_request"
    UNSUPPORTED_CONFIGURATION="unsupported_configuration"; CONTENT_BLOCKED="content_blocked"; CONTRACT_VIOLATION="contract_violation"
    PERMISSION_ERROR="permission_error"; CONNECTION_ERROR="connection_error"; PROVIDER_UNAVAILABLE="provider_unavailable"
    CONTEXT_LIMIT="context_limit"; UNSUPPORTED_CAPABILITY="unsupported_capability"; MALFORMED_RESPONSE="malformed_response"
    SCHEMA_VIOLATION="schema_violation"; UNKNOWN_PROVIDER_ERROR="unknown_provider_error"
    AUTHENTICATION_ERROR="authentication_error"; RATE_LIMITED="rate_limited"; INVALID_REQUEST="invalid_request"


RETRYABLE=frozenset({FailureCode.TIMEOUT,FailureCode.RATE_LIMIT,FailureCode.TRANSIENT_TRANSPORT,FailureCode.PROVIDER_5XX,
                     FailureCode.CONNECTION_ERROR,FailureCode.PROVIDER_UNAVAILABLE,FailureCode.RATE_LIMITED})


class ProviderFailure(Exception):
    def __init__(self, code: FailureCode, adapter_detail: str=""):
        del adapter_detail
        self.code=code
        super().__init__(code.value)


@dataclass(frozen=True)
class Usage:
    input_tokens: int; output_tokens: int; cache_read_tokens: int=0; cache_write_tokens: int=0
    def __post_init__(self):
        if any(type(v) is not int or not 0 <= v <= MAX_TOKENS for v in (self.input_tokens,self.output_tokens,self.cache_read_tokens,self.cache_write_tokens)): raise AuthoringError("invalid_request")
    @property
    def cached_tokens(self) -> int: return self.cache_read_tokens


@dataclass(frozen=True)
class Cost:
    amount: Decimal; currency: str; pricing_version: str; pricing_source: str
    def __post_init__(self):
        if type(self.amount) is not Decimal or not self.amount.is_finite() or self.amount < 0 or self.amount.as_tuple().exponent < -8: raise AuthoringError("invalid_request")
        if not re.fullmatch(r"[A-Z]{3}",self.currency): raise AuthoringError("unsupported_value")
        _identifier(self.pricing_version); _identifier(self.pricing_source)


def decimal_cost(value: str, currency: str, pricing_version: str, pricing_source: str) -> Cost:
    if type(value) is not str or "e" in value.lower(): raise AuthoringError("invalid_request")
    try: amount=Decimal(value)
    except InvalidOperation: raise AuthoringError("invalid_request") from None
    if format(amount,"f") != value: raise AuthoringError("invalid_request")
    return Cost(amount,currency,pricing_version,pricing_source)


@dataclass(frozen=True)
class ExecutionRequest:
    role: AuthoringRole; provider_id: str; model_id: str; settings: Mapping[str,Any]
    prompt: PromptSpecification; request_fingerprint: str; timeout_ms: int
    correlation_id: str; idempotency_key: str; retry_policy: RetryPolicy
    messages: tuple[Mapping[str,Any], ...] = ()
    output_schema: Mapping[str,Any] | None = None
    def __post_init__(self):
        _identifier(self.provider_id); _identifier(self.model_id); _identifier(self.correlation_id); _identifier(self.idempotency_key)
        if self.prompt.role != self.role or not HASH_PATTERN.fullmatch(self.request_fingerprint): raise AuthoringError("invalid_request")
        if type(self.timeout_ms) is not int or not 1 <= self.timeout_ms <= MAX_TIMEOUT_MS: raise AuthoringError("unsupported_value")
        object.__setattr__(self,"settings",freeze_json(self.settings))
        object.__setattr__(self,"messages",tuple(freeze_json(v) for v in self.messages))
        object.__setattr__(self,"output_schema",freeze_json(self.output_schema) if self.output_schema is not None else None)
    @property
    def route(self) -> ModelRoute: return ModelRoute(self.provider_id,self.model_id)


@dataclass(frozen=True)
class ProviderResult:
    technical_response: Mapping[str,Any]; provider_request_id: str; usage: Usage; cost: Cost; latency_ms: int
    def __post_init__(self):
        _identifier(self.provider_request_id,maximum=256)
        if type(self.latency_ms) is not int or not 0 <= self.latency_ms <= 2**31-1: raise AuthoringError("invalid_request")
        object.__setattr__(self,"technical_response",freeze_json(self.technical_response))
    @property
    def response_hash(self)->str: return hashlib.sha256(canonical_json_bytes(self.technical_response)).hexdigest()


@runtime_checkable
class AuthoringProvider(Protocol):
    async def execute(self, request: ExecutionRequest) -> ProviderResult: ...


class ProviderRegistry:
    """Thread-safe-after-construction lookup by stable provider id."""
    def __init__(self): self._providers: dict[str,AuthoringProvider] = {}
    def register(self, provider_id: str, provider: AuthoringProvider) -> None:
        _identifier(provider_id)
        if provider_id in self._providers: raise AuthoringError("duplicate_provider")
        self._providers[provider_id]=provider
    def get(self, provider_id: str) -> AuthoringProvider:
        _identifier(provider_id)
        try: return self._providers[provider_id]
        except KeyError: raise AuthoringError("unknown_provider") from None


@dataclass(frozen=True)
class Price:
    currency: str; pricing_version: str; pricing_source: str
    input_token_rate: Decimal; output_token_rate: Decimal
    cache_read_token_rate: Decimal = Decimal(0); cache_write_token_rate: Decimal = Decimal(0)
    def cost(self, usage: Usage) -> Cost:
        amount=(Decimal(usage.input_tokens)*self.input_token_rate + Decimal(usage.output_tokens)*self.output_token_rate +
                Decimal(usage.cache_read_tokens)*self.cache_read_token_rate + Decimal(usage.cache_write_tokens)*self.cache_write_token_rate)
        return Cost(amount.quantize(Decimal("0.00000001")),self.currency,self.pricing_version,self.pricing_source)


class PricingCatalog:
    def __init__(self, prices: Mapping[tuple[str,str],Price]): self._prices=MappingProxyType(dict(prices))
    def calculate(self, route: ModelRoute, usage: Usage) -> Cost:
        try: price=self._prices[(route.provider_id,route.model_id)]
        except KeyError: raise AuthoringError("unsupported_value") from None
        return price.cost(usage)


class FakeAuthoringProvider:
    """Deterministic contract probe; never generates task content."""
    def __init__(self, result: ProviderResult | None=None, failures: tuple[FailureCode,...]=()): self.result=result; self.failures=list(failures)
    async def execute(self, request: ExecutionRequest) -> ProviderResult:
        if self.failures: raise ProviderFailure(self.failures.pop(0),"discarded raw adapter prose")
        if self.result is not None: return self.result
        return ProviderResult({"schema_version":"authoring-provider-probe.v1","acknowledged":True},"fake-request",Usage(1,1),decimal_cost("0.00000000","USD","fake-v1","fake"),0)


async def invoke_provider(provider: AuthoringProvider, request: ExecutionRequest) -> ProviderResult:
    try: return await asyncio.wait_for(provider.execute(request), request.timeout_ms/1000)
    except TimeoutError: raise ProviderFailure(FailureCode.TIMEOUT) from None


class AuthoringExecutionService:
    """Narrow orchestration only; it creates no task, preview, or solver semantics."""
    def __init__(self, repository: Any, provider: AuthoringProvider | ProviderRegistry): self.repository=repository; self.provider=provider
    async def execute(self, session_id: Any, request: ExecutionRequest) -> Any:
        last=None
        for number in range(1,request.retry_policy.max_attempts+1):
            current=request if number==1 else ExecutionRequest(request.role,request.provider_id,request.model_id,request.settings,request.prompt,request.request_fingerprint,request.timeout_ms,request.correlation_id,f"{request.idempotency_key}-retry-{number}",request.retry_policy,request.messages,request.output_schema)
            attempt,created=await self.repository.create_attempt(session_id,current)
            if not created and attempt.status in {"succeeded","invalid_output","failed_terminal"}: return attempt
            if not await self.repository.claim(attempt.id): return attempt
            try:
                selected=self.provider.get(current.provider_id) if isinstance(self.provider,ProviderRegistry) else self.provider
                result=await invoke_provider(selected,current)
                await self.repository.finalize_success(attempt.id,result)
                return attempt
            except ProviderFailure as failure:
                await self.repository.finalize_failure(attempt.id,failure.code); last=attempt
                if failure.code not in RETRYABLE: return attempt
        return last
