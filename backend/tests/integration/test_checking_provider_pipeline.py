from uuid import uuid4
import pytest
from app.application.checking_provider import (AttemptDisposition, AttemptState, ContractProbe,
    Pricing, PromptSpec, ProviderExecutionKey, ProviderExecutionService, ProviderFailure,
    ProviderMessage, ProviderResponse, ProviderUsage, build_request)


def inputs():
    contract=ContractProbe(); prompt=PromptSpec("provider-probe","1.0.0","SYNTHETIC_ONLY",contract.schema_version)
    request=build_request(provider_id="fake",model_id="probe-v1",prompt=prompt,contract=contract,
        messages=(ProviderMessage("system","SYNTHETIC_ONLY"),),settings={"max_output_tokens":32})
    return ProviderExecutionKey(uuid4(),uuid4()),request,prompt,contract


class Store:
    def __init__(self): self.rows=[]; self.in_transaction=False; self.events=[]
    async def replay_or_claim(self,key,request,prompt,maximum_attempts):
        assert maximum_attempts==3
        if self.rows:
            row=self.rows[-1]
            if row.status=="running": return AttemptState(row.attempt_id,row.attempt_no,row.status,AttemptDisposition.RUNNING_EXISTING,row.request_fingerprint)
            return AttemptState(row.attempt_id,row.attempt_no,row.status,AttemptDisposition.TERMINAL_EXISTING,row.request_fingerprint,row.validated_output,row.error_code)
        row=AttemptState(uuid4(),1,"running",AttemptDisposition.CLAIMED,request.request_fingerprint); self.rows.append(row); self.events.append({"model_run_id":str(row.attempt_id),"attempt_no":1}); return row
    async def finalize(self,key,attempt,**values):
        assert not self.in_transaction
        row=AttemptState(attempt.attempt_id,attempt.attempt_no,values["status"],AttemptDisposition.TERMINAL_EXISTING,
            attempt.request_fingerprint,values["validated_output"],values["error_code"])
        self.rows[-1]=row; self.latency=values["measured_latency_ms"]; self.response=values["response"]; return row


@pytest.mark.asyncio
async def test_service_key_privacy_transaction_boundary_latency_and_terminal_replay():
    key,request,prompt,contract=inputs(); store=Store(); clock=iter([10.0,10.125])
    class Fake:
        calls=0
        async def evaluate(self,outbound):
            self.calls+=1; assert outbound is request and str(key.check_run_id) not in repr(outbound); assert not store.in_transaction
            return ProviderResponse("synthetic-request",'{"schema_version":"provider-contract-probe.v1","acknowledged":true}',usage=ProviderUsage(2,1),latency_ms=999)
    fake=Fake(); service=ProviderExecutionService(store,fake,monotonic=lambda:next(clock))
    outcome=await service.execute(key,request,prompt,contract)
    assert outcome.state=="succeeded" and fake.calls==1 and store.latency==125 and store.response.latency_ms==125
    assert not hasattr(outcome,"raw_output") and "SYNTHETIC_ONLY" not in str(store.events)
    assert (await service.execute(key,request,prompt,contract)).state=="succeeded" and fake.calls==1


@pytest.mark.asyncio
async def test_running_replay_does_not_call_provider_and_uses_explicit_disposition():
    key,request,prompt,contract=inputs(); store=Store()
    store.rows=[AttemptState(uuid4(),1,"running",AttemptDisposition.CLAIMED,request.request_fingerprint)]
    class Never:
        async def evaluate(self,request): raise AssertionError("provider called")
    outcome=await ProviderExecutionService(store,Never()).execute(key,request,prompt,contract)
    assert outcome.state=="in_progress"


@pytest.mark.asyncio
async def test_backward_clock_leaves_claim_running_without_sensitive_error():
    key,request,prompt,contract=inputs(); store=Store(); clock=iter([2.0,1.0])
    class Fake:
        async def evaluate(self,request): return ProviderResponse("r",'{"schema_version":"provider-contract-probe.v1","acknowledged":true}')
    with pytest.raises(ValueError) as exc: await ProviderExecutionService(store,Fake(),monotonic=lambda:next(clock)).execute(key,request,prompt,contract)
    assert store.rows[-1].status=="running" and "acknowledged" not in str(exc.value)
