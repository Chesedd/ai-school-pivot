import asyncio,json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
import pytest
from app.application.checking_acceptance import AcceptanceContractError,AcceptanceThresholdPolicy,GoldenDatasetV1,evaluate_golden_dataset,execute_golden_case
FIXTURE=Path(__file__).parents[1]/"fixtures"/"checking_golden_v1.json"
def dataset(): return GoldenDatasetV1.from_dict(json.loads(FIXTURE.read_text()))
def observations(ds): return asyncio.run(_observations(ds))
async def _observations(ds): return tuple([await execute_golden_case(x.input,x.case_id) for x in ds.cases])
def test_all_60_cases_are_executable_and_distributed():
 d=dataset(); assert len(d.cases)==60; assert {c:sum(x.category==c for x in d.cases) for c in {x.category for x in d.cases}}=={"exact_text":8,"choice":12,"numeric":10,"structured_expression":10,"llm_rubric":12,"boundary":8}; assert all(x.input["snapshot"]["items"] for x in d.cases)
def test_exact_fingerprint_and_all_required_gates():
 d=dataset();r=evaluate_golden_dataset(d,observations(d),AcceptanceThresholdPolicy()); assert r.accepted; assert r.corpus_fingerprint=="41a3aadd87c40b5e41e85ea878f1ca573d8925efe1ac69614995ca3145687a8b"; assert (r.metrics.finding_identity_agreement,r.metrics.maximum_score_agreement,r.metrics.confidence_agreement)==(Decimal("1.0000"),)*3
@pytest.mark.parametrize('field,value',[('confidence',Decimal('0')),('structured_output_valid',False),('provider_failed',True)])
def test_per_case_output_gates_are_fatal(field,value):
 d=dataset();obs=list(observations(d)); from dataclasses import replace; x=obs[0]
 changed=replace(x,**{field:value})
 obs[0]=changed; assert not evaluate_golden_dataset(d,obs,AcceptanceThresholdPolicy()).accepted
def test_missing_and_unexpected_are_fatal():
 d=dataset();obs=observations(d); assert not evaluate_golden_dataset(d,obs[:-1],AcceptanceThresholdPolicy()).accepted
 with pytest.raises(AcceptanceContractError): evaluate_golden_dataset(d,obs+(obs[0],),AcceptanceThresholdPolicy())
def test_detached_immutable_input():
 raw=json.loads(FIXTURE.read_text());d=GoldenDatasetV1.from_dict(raw);raw['cases'][0]['input']['snapshot']['items'][0]['raw_answer']='changed';assert d.cases[0].input['snapshot']['items'][0]['raw_answer']!='changed'
 with pytest.raises(TypeError): d.cases[0].input['x']=1
 with pytest.raises(FrozenInstanceError): d.cases[0].case_id='x'
