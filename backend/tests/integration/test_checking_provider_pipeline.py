import pytest
from app.application.checking_provider import (ContractProbe, PromptSpec, ProviderMessage,
    ProviderResponse, build_request, run_with_retries)


@pytest.mark.asyncio
async def test_synthetic_provider_pipeline_and_replay():
    class Fake:
        calls=0
        async def evaluate(self, request):
            self.calls+=1
            assert "submission" not in repr(request) and "student" not in repr(request)
            return ProviderResponse("synthetic-request",'{"schema_version":"provider-contract-probe.v1","acknowledged":true}')
    contract=ContractProbe(); fake=Fake(); messages=(ProviderMessage("system","SYNTHETIC_ONLY"),)
    request=build_request(provider_id="fake",model_id="probe-v1",
        prompt=PromptSpec("provider-probe","1.0.0","SYNTHETIC_ONLY",contract.schema_version),
        contract=contract,messages=messages,settings={"max_output_tokens":32})
    persisted={}
    async def execute():
        if request.request_fingerprint not in persisted:
            persisted[request.request_fingerprint]=await run_with_retries(fake,request,contract)
        return persisted[request.request_fingerprint]
    assert (await execute())["candidate"]["acknowledged"] is True
    assert await execute()==await execute() and fake.calls==1
    assert messages[0].content=="SYNTHETIC_ONLY"
