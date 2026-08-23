from decimal import Decimal
from types import SimpleNamespace as NS
import sys
import pytest

from app.application.authoring import *
from app.infrastructure.authoring_providers import AnthropicAdapter, OpenAIAdapter, UnconfiguredProvider

SCHEMA={"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":False}

def prompt(): return PromptSpecification("probe",AuthoringRole.GENERATOR,"1","1","a"*64,"probe.v1","policy")
def execution(provider,model):
    return ExecutionRequest(AuthoringRole.GENERATOR,provider,model,{"max_output_tokens":64},prompt(),"a"*64,1000,"corr","key",RetryPolicy(1),
                            ({"role":"system","content":"system"},{"role":"user","content":"probe"}),SCHEMA)
def catalog(provider,model):
    return PricingCatalog({(provider,model):Price("USD","2026-08","test",Decimal("0.01"),Decimal("0.02"),Decimal("0.005"),Decimal("0.006"))})

class Call:
    def __init__(self,response=None,error=None): self.response=response; self.error=error; self.kwargs=None
    async def create(self,**kwargs):
        self.kwargs=kwargs
        if self.error: raise self.error
        return self.response

async def test_openai_responses_structured_output_and_normalization():
    call=Call(NS(id="resp_1",output_text='{"ok":true}',usage=NS(input_tokens=10,output_tokens=2,input_tokens_details=NS(cached_tokens=3))))
    result=await OpenAIAdapter(NS(responses=call),catalog("openai","gpt-test")).execute(execution("openai","gpt-test"))
    assert call.kwargs["model"]=="gpt-test" and call.kwargs["text"]["format"]=={"type":"json_schema","name":"probe.v1","schema":SCHEMA,"strict":True}
    assert result.technical_response["ok"] is True and result.provider_request_id=="resp_1"
    assert result.usage==Usage(7,2,3,0) and result.usage.cached_tokens==result.usage.cache_read_tokens
    assert result.cost.amount==Decimal("0.12500000")

async def test_anthropic_messages_structured_output_and_normalization():
    call=Call(NS(id="msg_1",content=[NS(type="text",text='{"ok":true}')],usage=NS(input_tokens=11,output_tokens=4,cache_read_input_tokens=2,cache_creation_input_tokens=1)))
    result=await AnthropicAdapter(NS(messages=call),catalog("anthropic","claude-test")).execute(execution("anthropic","claude-test"))
    assert call.kwargs["model"]=="claude-test" and call.kwargs["system"]=="system"
    assert call.kwargs["output_config"]=={"format":{"type":"json_schema","schema":SCHEMA}}
    assert result.usage==Usage(11,4,2,1) and result.provider_request_id=="msg_1"

@pytest.mark.parametrize("adapter,provider,response,expected",[
    (OpenAIAdapter,"openai",NS(id="zero",output_text='{"ok":true}',usage=NS(input_tokens=0,output_tokens=0)),Usage(0,0,0,0)),
    (OpenAIAdapter,"openai",NS(id="plain",output_text='{"ok":true}',usage=NS(input_tokens=5,output_tokens=2)),Usage(5,2,0,0)),
    (AnthropicAdapter,"anthropic",NS(id="plain",content=[NS(type="text",text='{"ok":true}')],usage=NS(input_tokens=5,output_tokens=2)),Usage(5,2,0,0)),
    (AnthropicAdapter,"anthropic",NS(id="write",content=[NS(type="text",text='{"ok":true}')],usage=NS(input_tokens=5,output_tokens=2,cache_creation_input_tokens=4)),Usage(5,2,0,4)),
])
async def test_usage_no_cache_zero_and_cache_write(adapter,provider,response,expected):
    call=Call(response); client=NS(responses=call) if provider=="openai" else NS(messages=call)
    result=await adapter(client,catalog(provider,"model")).execute(execution(provider,"model"))
    assert result.usage==expected

async def test_openai_rejects_cached_input_larger_than_total():
    response=NS(id="bad",output_text='{"ok":true}',usage=NS(input_tokens=2,output_tokens=1,input_tokens_details=NS(cached_tokens=3)))
    with pytest.raises(ProviderFailure) as error:
        await OpenAIAdapter(NS(responses=Call(response)),catalog("openai","model")).execute(execution("openai","model"))
    assert error.value.code is FailureCode.MALFORMED_RESPONSE

@pytest.mark.parametrize("adapter,provider",[(OpenAIAdapter,"openai"),(AnthropicAdapter,"anthropic")])
async def test_adapter_contract_malformed_response(adapter,provider):
    response=NS(id="bad",output_text="not-json",usage=NS(input_tokens=1,output_tokens=1),content=[NS(type="text",text="not-json")])
    call=Call(response); client=NS(responses=call) if provider=="openai" else NS(messages=call)
    with pytest.raises(ProviderFailure) as error: await adapter(client,catalog(provider,"model")).execute(execution(provider,"model"))
    assert error.value.code is FailureCode.MALFORMED_RESPONSE

@pytest.mark.parametrize("adapter,provider",[(OpenAIAdapter,"openai"),(AnthropicAdapter,"anthropic")])
async def test_structured_output_is_required_without_prompt_fallback(adapter,provider):
    request=execution(provider,"model")
    request=ExecutionRequest(request.role,request.provider_id,request.model_id,request.settings,request.prompt,request.request_fingerprint,request.timeout_ms,request.correlation_id,request.idempotency_key,request.retry_policy,request.messages,None)
    client=NS(responses=Call()) if provider=="openai" else NS(messages=Call())
    with pytest.raises(ProviderFailure) as error: await adapter(client,catalog(provider,"model")).execute(request)
    assert error.value.code is FailureCode.UNSUPPORTED_CAPABILITY

async def test_registry_is_stable_and_fail_closed():
    registry=ProviderRegistry(); fake=FakeAuthoringProvider(); registry.register("fake",fake)
    assert registry.get("fake") is fake
    with pytest.raises(AuthoringError,match="duplicate_provider"): registry.register("fake",fake)
    with pytest.raises(AuthoringError,match="unknown_provider"): registry.get("openai")
async def test_unconfigured_registry_is_provider_local_and_has_no_fallback():
    from app.infrastructure.authoring_providers import production_registry
    registry=production_registry(catalog("openai","model"))
    for provider in ("openai","anthropic"):
        with pytest.raises(ProviderFailure) as error:
            await registry.get(provider).execute(execution(provider,"model"))
        assert error.value.code is FailureCode.AUTHENTICATION_ERROR

def test_one_configured_provider_does_not_require_the_other(monkeypatch):
    from app.infrastructure.authoring_providers import production_registry
    monkeypatch.setitem(sys.modules,"openai",NS(AsyncOpenAI=lambda **kwargs: NS(responses=NS(),configuration=kwargs)))
    monkeypatch.setitem(sys.modules,"anthropic",NS(AsyncAnthropic=lambda **kwargs: NS(messages=NS(),configuration=kwargs)))
    openai_registry=production_registry(catalog("openai","model"),openai_api_key="configured")
    assert isinstance(openai_registry.get("openai"),OpenAIAdapter)
    assert isinstance(openai_registry.get("anthropic"),UnconfiguredProvider)
    anthropic_registry=production_registry(catalog("anthropic","model"),anthropic_api_key="configured")
    assert isinstance(anthropic_registry.get("openai"),UnconfiguredProvider)
    assert isinstance(anthropic_registry.get("anthropic"),AnthropicAdapter)
