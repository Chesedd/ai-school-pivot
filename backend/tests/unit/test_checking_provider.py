import asyncio
from dataclasses import FrozenInstanceError
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest

from app.application.checking_provider import (AttemptDisposition, AttemptState, ContractProbe,
    LLMProvider, Pricing, PromptSpec, ProviderBoundaryError, ProviderExecutionKey,
    ProviderFailure, ProviderMessage, ProviderRequest, ProviderResponse, ProviderUsage,
    build_request, canonical_json, freeze_json, measured_milliseconds, run_with_retries,
    settings_snapshot, thaw_json, validate_response)


def request(messages=None, settings=None, contract=None):
    contract=contract or ContractProbe()
    return build_request(provider_id="fake", model_id="probe-v1",
        prompt=PromptSpec("provider-probe", "1.0.0", "exact\ntext", contract.schema_version),
        contract=contract, messages=messages or (ProviderMessage("system", "SYNTHETIC_SYSTEM"),
        ProviderMessage("user", "SYNTHETIC_USER")), settings=settings or {"temperature": "0.0", "seed": 7})


def test_protocol_key_immutability_hash_and_fingerprint():
    class Fake:
        async def evaluate(self, request): raise NotImplementedError
    assert isinstance(Fake(), LLMProvider)
    req=request(); assert req==request()
    assert req.prompt.template_hash=="3fec1cbb170f45d04d9f722ffc1e65c0d7863a28f716c165bb53c48118881b05"
    with pytest.raises(FrozenInstanceError): req.timeout_ms=1
    key=ProviderExecutionKey(uuid4(),uuid4())
    assert not hasattr(req,"check_run_id") and str(key.check_run_id) not in repr(req)
    assert req.request_fingerprint!=request((ProviderMessage("user","SYNTHETIC_USER"),ProviderMessage("system","SYNTHETIC_SYSTEM"))).request_fingerprint


def test_execution_key_canonicalizes_driver_uuid_subclasses_and_remains_frozen():
    class DriverUUID(UUID): pass
    run_id=uuid4(); item_id=uuid4(); driver_run_id=DriverUUID(bytes=run_id.bytes)
    key=ProviderExecutionKey(driver_run_id,item_id)
    assert type(key.check_run_id) is UUID and type(key.assessment_item_id) is UUID
    assert key.check_run_id==run_id and key.check_run_id.bytes==driver_run_id.bytes
    assert key.assessment_item_id==item_id and key.assessment_item_id.bytes==item_id.bytes
    with pytest.raises(FrozenInstanceError): key.check_run_id=uuid4()
    req=request()
    assert not hasattr(req,"check_run_id") and str(run_id) not in repr(req)


@pytest.mark.parametrize("invalid",[str(uuid4()),1,object(),None])
def test_execution_key_rejects_non_uuid_values(invalid):
    with pytest.raises(ProviderBoundaryError,match="^invalid execution key$"):
        ProviderExecutionKey(invalid,uuid4())


def test_recursive_json_freeze_thaw_is_detached_and_canonical():
    original={"nested":{"items":[{"value":1}]}}
    frozen=freeze_json(original); original["nested"]["items"][0]["value"]=9
    assert frozen["nested"]["items"][0]["value"]==1
    with pytest.raises(TypeError): frozen["nested"]["items"][0]["value"]=2
    plain=thaw_json(frozen); assert type(plain) is dict and type(plain["nested"]["items"]) is list
    plain["nested"]["items"][0]["value"]=3; assert frozen["nested"]["items"][0]["value"]==1
    assert canonical_json(frozen)=='{"nested":{"items":[{"value":1}]}}'
    for bad in (1.0,Decimal("1"),object(),{"x":{1}}):
        with pytest.raises(ProviderBoundaryError): freeze_json(bad)


def test_nested_schema_response_and_state_are_deeply_immutable():
    schema=ContractProbe().json_schema(); req=request(); schema["properties"]["acknowledged"]["type"]="string"
    assert req.schema_document["properties"]["acknowledged"]["type"]=="boolean"
    with pytest.raises(TypeError): req.schema_document["properties"]["acknowledged"]["type"]="string"
    output=validate_response(ProviderResponse("req-1",'{"schema_version":"provider-contract-probe.v1","acknowledged":true}'),ContractProbe())
    state=AttemptState(uuid4(),1,"succeeded",AttemptDisposition.TERMINAL_EXISTING,"a"*64,output)
    with pytest.raises(TypeError): state.validated_output["candidate"]["acknowledged"]=False


@pytest.mark.parametrize("bad",[{"temperature":0.0},{"temperature":"2e-1"},{"seed":True},{"max_output_tokens":0},{"other":1}])
def test_settings_are_strict(bad):
    with pytest.raises(ProviderBoundaryError): settings_snapshot(bad)


def test_strict_response_validation_and_safe_errors():
    good='{"schema_version":"provider-contract-probe.v1","acknowledged":true}'
    assert validate_response(ProviderResponse("req-1",good),ContractProbe())["candidate"]["acknowledged"] is True
    for raw,code in [("```json\n{}\n```","invalid_json"),('{"schema_version":"provider-contract-probe.v1","acknowledged":1}',"schema_invalid"),('{"schema_version":"provider-contract-probe.v1","acknowledged":true,"extra":1}',"schema_invalid")]:
        with pytest.raises(ProviderFailure) as exc: validate_response(ProviderResponse("req-1",raw),ContractProbe())
        assert exc.value.code==code and raw not in str(exc.value)
    with pytest.raises(ProviderFailure): validate_response(ProviderResponse("req-1",good,{"acknowledged":False}),ContractProbe())


def test_usage_metadata_decimal_cost_and_clock():
    for values in [(-1,0,0),(True,0,0)]:
        with pytest.raises(ProviderBoundaryError): ProviderUsage(*values)
    price=Pricing("USD","2026-08","local",Decimal("0.000000004"),Decimal("0.1"),Decimal("0"))
    assert price.cost(ProviderUsage(1,2,3))==Decimal("0.20000000")
    assert measured_milliseconds(1.0,1.125)==125
    for pair in [(True,2),(2,1),(float("inf"),3)]:
        with pytest.raises(ProviderBoundaryError): measured_milliseconds(*pair)


async def record(target,value): target.append(value)


@pytest.mark.asyncio
async def test_retry_counts_jitter_and_measured_clock():
    class Fake:
        def __init__(self,failures): self.failures=list(failures); self.calls=0
        async def evaluate(self,req):
            self.calls+=1
            if self.failures: raise ProviderFailure(self.failures.pop(0))
            return ProviderResponse("req-1",'{"schema_version":"provider-contract-probe.v1","acknowledged":true}',latency_ms=999)
    delays=[]; fake=Fake(["transport","transport"]); clock=iter([1.0,1.1,2.0,2.1,3.0,3.25])
    await run_with_retries(fake,request(),ContractProbe(),sleeper=lambda n:record(delays,n),jitter=lambda:.5,monotonic=lambda:next(clock))
    assert fake.calls==3 and delays==[.5,1.0]
    for failures,calls in [(["invalid_json","invalid_json"],2),(["authentication"],1),(["schema_invalid"],1),(["content_blocked"],1),(["unknown"],1)]:
        fake=Fake(failures)
        with pytest.raises(ProviderFailure): await run_with_retries(fake,request(),ContractProbe(),sleeper=lambda n:record([],n))
        assert fake.calls==calls


@pytest.mark.asyncio
async def test_application_timeout_is_enforced():
    class Slow:
        async def evaluate(self,req): await asyncio.sleep(.1)
    req=request(); object.__setattr__(req,"timeout_ms",1)
    with pytest.raises(ProviderFailure) as exc: await run_with_retries(Slow(),req,ContractProbe(),sleeper=lambda n:record([],n))
    assert exc.value.code=="timeout"
