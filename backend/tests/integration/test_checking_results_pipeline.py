from decimal import Decimal
from uuid import UUID
import pytest
from app.application.checking_results import CheckingResultFinalizationService, ConfidenceGatePolicy, RunObservability, OBSERVABILITY_SCHEMA_VERSION

class Store:
    async def finalize(self,run,version,policy,drafts):
        return RunObservability(OBSERVABILITY_SCHEMA_VERSION,run,"completed",policy.semantic_version,len(drafts),len(drafts),0,0,(),(),())

@pytest.mark.asyncio
async def test_transport_neutral_finalization_service():
    policy=ConfidenceGatePolicy("p1",Decimal(".5"),Decimal(".1"),Decimal(".1"),Decimal(".1"),Decimal(".1")); run=UUID(int=1)
    value=await CheckingResultFinalizationService(Store()).finalize(run,1,policy,())
    assert value.run_id==run and value.schema_version==OBSERVABILITY_SCHEMA_VERSION
