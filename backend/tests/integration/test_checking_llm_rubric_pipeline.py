"""Database-free pipeline proof using the real Phase 4.7 execution service."""
import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.application.checking_llm_rubric import ConfidencePolicy, LLMRubricChecker, OUTPUT_SCHEMA_VERSION, SYSTEM_MESSAGE
from app.application.checking_provider import (AttemptDisposition, AttemptState, PromptSpec,
    ProviderExecutionKey, ProviderExecutionService, ProviderResponse)
from app.application.checking_deterministic import execute_deterministic
from app.application.checking_routing import CheckerRequest, CheckerType, route_snapshot
from tests.unit.test_checking_llm_rubric import ITEM, RID, RUN, candidate, item


class Store:
    async def replay_or_claim(self,key,request,prompt,maximum_attempts):
        self.request=request
        return AttemptState(uuid4(),1,"running",AttemptDisposition.CLAIMED,request.request_fingerprint)
    async def finalize(self,key,attempt,**values):
        return AttemptState(attempt.attempt_id,1,values["status"],AttemptDisposition.TERMINAL_EXISTING,
                            attempt.request_fingerprint,values["validated_output"],values["error_code"])


@pytest.mark.asyncio
async def test_ready_route_executes_real_provider_boundary_and_materializes_result():
    output=candidate(); store=Store()
    class FakeProvider:
        calls=0
        async def evaluate(self,request):
            self.calls+=1
            assert str(RUN) not in repr(request) and str(ITEM) not in repr(request)
            return ProviderResponse("synthetic",json.dumps(output,separators=(",",":")))
    provider=FakeProvider(); service=ProviderExecutionService(store,provider,monotonic=iter([1.0,1.1]).__next__)
    snapshot={"snapshot_schema_version":"checking_input_v1","handoff_version":1,
      "routing_contract_version":"checking_routing_contract_v1","items":[item()]}
    decision=route_snapshot(snapshot)[0]
    prompt=PromptSpec("checking.llm-rubric","1.0.0",SYSTEM_MESSAGE,OUTPUT_SCHEMA_VERSION)
    checker=LLMRubricChecker(service,ProviderExecutionKey(UUID(RUN),UUID(ITEM)),provider_id="fake",model_id="fake-v1",
      prompt=prompt,settings={"temperature":"0"},confidence_policy=ConfidencePolicy("v1",Decimal("0.8"),("base",)))
    result=await execute_deterministic(CheckerRequest(snapshot["items"][0],decision),{CheckerType.LLM_RUBRIC:checker})
    assert result.score_suggested==Decimal("5.00") and result.needs_human_review and provider.calls==1
