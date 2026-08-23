"""Vendor SDK adapters. No SDK type crosses this module's boundary."""
from __future__ import annotations

import json
from time import monotonic
from typing import Any, Mapping

from app.application.authoring import (ExecutionRequest, FailureCode, ModelRoute, PricingCatalog,
    ProviderCapabilities, ProviderFailure, ProviderRegistry, ProviderResult, Usage, thaw_json)


def _schema(request: ExecutionRequest) -> dict[str,Any]:
    if request.output_schema is None: raise ProviderFailure(FailureCode.UNSUPPORTED_CAPABILITY)
    return thaw_json(request.output_schema)


def _messages(request: ExecutionRequest) -> list[dict[str,Any]]:
    if not request.messages: raise ProviderFailure(FailureCode.INVALID_PROVIDER_REQUEST)
    return [thaw_json(value) for value in request.messages]


def _failure(exc: Exception) -> ProviderFailure:
    # Classification is deliberately based on stable HTTP semantics, not leaked SDK errors.
    status=getattr(exc,"status_code",None)
    name=type(exc).__name__.lower()
    if status == 401: code=FailureCode.AUTHENTICATION_ERROR
    elif status == 403: code=FailureCode.PERMISSION_ERROR
    elif status == 429: code=FailureCode.RATE_LIMITED
    elif status in (408,504) or "timeout" in name: code=FailureCode.TIMEOUT
    elif "connection" in name: code=FailureCode.CONNECTION_ERROR
    elif status is not None and status >= 500: code=FailureCode.PROVIDER_UNAVAILABLE
    elif status == 413 or (status == 400 and "context" in str(exc).lower()): code=FailureCode.CONTEXT_LIMIT
    elif status is not None and 400 <= status < 500: code=FailureCode.INVALID_REQUEST
    else: code=FailureCode.UNKNOWN_PROVIDER_ERROR
    return ProviderFailure(code)


class OpenAIAdapter:
    capabilities=ProviderCapabilities(True,True,True)
    def __init__(self, client: Any, pricing: PricingCatalog): self._client=client; self._pricing=pricing
    async def execute(self, request: ExecutionRequest) -> ProviderResult:
        started=monotonic()
        try:
            response=await self._client.responses.create(model=request.model_id,input=_messages(request),
                text={"format":{"type":"json_schema","name":request.prompt.output_schema_version,
                                "schema":_schema(request),"strict":True}},
                timeout=request.timeout_ms/1000,**thaw_json(request.settings))
            raw=response.output_text; payload=json.loads(raw)
            if type(payload) is not dict: raise ValueError
            details=getattr(response.usage,"input_tokens_details",None)
            usage=Usage(response.usage.input_tokens,response.usage.output_tokens,
                        getattr(details,"cached_tokens",0) or 0,0)
            return ProviderResult(payload,response.id,usage,self._pricing.calculate(request.route,usage),
                                  max(0,int((monotonic()-started)*1000)))
        except ProviderFailure: raise
        except (json.JSONDecodeError,ValueError,AttributeError,TypeError): raise ProviderFailure(FailureCode.MALFORMED_RESPONSE) from None
        except Exception as exc: raise _failure(exc) from None


class AnthropicAdapter:
    capabilities=ProviderCapabilities(True,True,True)
    def __init__(self, client: Any, pricing: PricingCatalog): self._client=client; self._pricing=pricing
    async def execute(self, request: ExecutionRequest) -> ProviderResult:
        started=monotonic(); messages=_messages(request); system=None
        if messages and messages[0].get("role")=="system": system=messages.pop(0).get("content")
        settings=thaw_json(request.settings); max_tokens=settings.pop("max_tokens",settings.pop("max_output_tokens",1024))
        try:
            response=await self._client.messages.create(model=request.model_id,messages=messages,system=system,
                max_tokens=max_tokens,output_config={"format":{"type":"json_schema","schema":_schema(request)}},
                timeout=request.timeout_ms/1000,**settings)
            raw="".join(block.text for block in response.content if getattr(block,"type",None)=="text")
            payload=json.loads(raw)
            if type(payload) is not dict: raise ValueError
            usage=Usage(response.usage.input_tokens,response.usage.output_tokens,
                        getattr(response.usage,"cache_read_input_tokens",0) or 0,
                        getattr(response.usage,"cache_creation_input_tokens",0) or 0)
            return ProviderResult(payload,response.id,usage,self._pricing.calculate(request.route,usage),
                                  max(0,int((monotonic()-started)*1000)))
        except ProviderFailure: raise
        except (json.JSONDecodeError,ValueError,AttributeError,TypeError): raise ProviderFailure(FailureCode.MALFORMED_RESPONSE) from None
        except Exception as exc: raise _failure(exc) from None


class UnconfiguredProvider:
    capabilities=ProviderCapabilities()
    async def execute(self, request: ExecutionRequest) -> ProviderResult:
        del request
        raise ProviderFailure(FailureCode.AUTHENTICATION_ERROR)


def production_registry(pricing: PricingCatalog, *, openai_api_key: str|None=None,
                        anthropic_api_key: str|None=None) -> ProviderRegistry:
    registry=ProviderRegistry()
    if openai_api_key:
        from openai import AsyncOpenAI
        registry.register("openai",OpenAIAdapter(AsyncOpenAI(api_key=openai_api_key),pricing))
    else: registry.register("openai",UnconfiguredProvider())
    if anthropic_api_key:
        from anthropic import AsyncAnthropic
        registry.register("anthropic",AnthropicAdapter(AsyncAnthropic(api_key=anthropic_api_key),pricing))
    else: registry.register("anthropic",UnconfiguredProvider())
    return registry
