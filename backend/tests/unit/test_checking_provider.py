import asyncio
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.application.checking_provider import (ContractProbe, LLMProvider, Pricing,
    PromptSpec, ProviderBoundaryError, ProviderFailure, ProviderMessage, ProviderResponse,
    ProviderUsage, build_request, run_with_retries, settings_snapshot, validate_response)


def request(messages=None, settings=None):
    return build_request(provider_id="fake", model_id="probe-v1",
        prompt=PromptSpec("provider-probe", "1.0.0", "exact\ntext", "provider-contract-probe.v1"),
        contract=ContractProbe(), messages=messages or (ProviderMessage("system", "SYNTHETIC_SYSTEM"),
        ProviderMessage("user", "SYNTHETIC_USER")), settings=settings or {"temperature": "0.0", "seed": 7})


def test_protocol_immutability_hash_and_fingerprint():
    class Fake:
        async def evaluate(self, request): raise NotImplementedError
    assert isinstance(Fake(), LLMProvider)
    req = request(); assert req == request()
    assert req.prompt.template_hash == "3fec1cbb170f45d04d9f722ffc1e65c0d7863a28f716c165bb53c48118881b05"
    with pytest.raises(FrozenInstanceError): req.timeout_ms = 1
    assert req.request_fingerprint != request((ProviderMessage("user", "SYNTHETIC_USER"),
        ProviderMessage("system", "SYNTHETIC_SYSTEM"))).request_fingerprint


@pytest.mark.parametrize("bad", [{"temperature": 0.0}, {"temperature": "2e-1"},
    {"seed": True}, {"max_output_tokens": 0}, {"other": 1}])
def test_settings_are_strict(bad):
    with pytest.raises(ProviderBoundaryError): settings_snapshot(bad)


def test_strict_response_validation():
    good='{"schema_version":"provider-contract-probe.v1","acknowledged":true}'
    result=validate_response(ProviderResponse("req-1",good, {"acknowledged":True,
        "schema_version":"provider-contract-probe.v1"}),ContractProbe())
    assert result["candidate"]["acknowledged"] is True
    for raw, code in [("```json\n{}\n```","invalid_json"),
        ('{"schema_version":"provider-contract-probe.v1","acknowledged":1}',"schema_invalid"),
        ('{"schema_version":"provider-contract-probe.v1","acknowledged":true,"extra":1}',"schema_invalid")]:
        with pytest.raises(ProviderFailure) as exc: validate_response(ProviderResponse("req-1",raw),ContractProbe())
        assert exc.value.code == code and raw not in str(exc.value)
    with pytest.raises(ProviderFailure): validate_response(ProviderResponse("req-1",good,{"acknowledged":False}),ContractProbe())


def test_usage_metadata_and_decimal_cost():
    for values in [(-1,0,0),(True,0,0)]:
        with pytest.raises(ProviderBoundaryError): ProviderUsage(*values)
    usage=ProviderUsage(1,2,3)
    price=Pricing("USD","2026-08","local",Decimal("0.000000004"),Decimal("0.1"),Decimal("0"))
    assert price.cost(usage)==Decimal("0.20000000")


@pytest.mark.asyncio
async def test_retry_counts_and_jitter():
    class Fake:
        def __init__(self, failures): self.failures=list(failures); self.calls=0
        async def evaluate(self, req):
            self.calls+=1
            if self.failures: raise ProviderFailure(self.failures.pop(0))
            return ProviderResponse("req-1",'{"schema_version":"provider-contract-probe.v1","acknowledged":true}')
    delays=[]; fake=Fake(["transport","transport"])
    await run_with_retries(fake,request(),ContractProbe(),sleeper=lambda n: _record(delays,n),jitter=lambda:.5)
    assert fake.calls==3 and delays==[.5,1.0]
    for failures, calls in [(["invalid_json","invalid_json"],2),(["authentication"],1),(["schema_invalid"],1),(["unknown"],1)]:
        fake=Fake(failures)
        with pytest.raises(ProviderFailure): await run_with_retries(fake,request(),ContractProbe(),sleeper=lambda n:_record([],n))
        assert fake.calls==calls


async def _record(target,value): target.append(value)


@pytest.mark.asyncio
async def test_application_timeout_is_enforced():
    class Slow:
        async def evaluate(self, req): await asyncio.sleep(.1)
    req=request(); object.__setattr__(req,"timeout_ms",1)
    with pytest.raises(ProviderFailure) as exc: await run_with_retries(Slow(),req,ContractProbe(),sleeper=lambda n:_record([],n))
    assert exc.value.code=="timeout"
