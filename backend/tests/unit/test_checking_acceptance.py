import asyncio, json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
import pytest
from app.application.checking_acceptance import AcceptanceContractError,AcceptanceThresholdPolicy,GoldenDatasetV2,evaluate_golden_dataset
from tests.support.checking_acceptance_executor import execute_golden_case
FIXTURE=Path(__file__).parents[1]/"fixtures"/"checking_golden_v2.json"
def dataset(): return GoldenDatasetV2.from_dict(json.loads(FIXTURE.read_text()))
def observations(ds): return asyncio.run(_observations(ds))
async def _observations(ds): return tuple([await execute_golden_case(x.input,x.case_id) for x in ds.cases])
def test_all_60_cases_are_executable_and_distributed():
 d=dataset(); assert len(d.cases)==60; assert {c:sum(x.category==c for x in d.cases) for c in {x.category for x in d.cases}}=={"exact_text":8,"choice":12,"numeric":10,"structured_expression":10,"llm_rubric":12,"boundary":8}; assert all(x.input["snapshot"]["items"] for x in d.cases)
def test_required_variants_are_structural_and_behavioral():
 d=dataset(); cases={c.case_id:c for c in d.cases}; item=lambda n:cases[f"case-{n:03d}"].input["snapshot"]["items"][0]
 for n in (12,13,14,17,18,19,20): assert item(n)["answer_format"]=="multiple_choice" and isinstance(item(n)["raw_answer"],tuple)
 for n in (15,16): assert len(item(n)["methodology"]["accepted_answers"])>=2
 for n in (18,19):
  method=item(n)["methodology"]; policy=method["choice_scoring_policy"]
  assert policy["mode"]=="per_option" and {r["option_id"] for r in policy["option_rules"]}=={o["id"] for o in method["choice_options"]}
 assert cases["case-019"].expected_outcome=="partially_correct" and Decimal(0)<cases["case-019"].expected_score<cases["case-019"].max_score
 assert Decimal(item(25)["raw_answer"])<0 and Decimal(item(25)["methodology"]["accepted_answers"][0]["canonical_decimal"])<0
 assert Decimal(item(26)["raw_answer"])==Decimal(item(26)["methodology"]["accepted_answers"][0]["canonical_decimal"])==0
 assert cases["case-028"].expected_outcome=="insufficient_rubric" and cases["case-040"].expected_outcome=="manual_required"
 assert (cases["case-042"].expected_outcome,cases["case-043"].expected_outcome)==("partially_correct","incorrect")
 assert all(cases[f"case-{n:03d}"].expected_outcome=="unclear" for n in (47,48,49))
 assert cases["case-045"].expected_findings[0]["finding_type"]=="typical_error"
 assert cases["case-046"].expected_findings[0]["finding_type"]=="rubric"
 assert "IGNORE ALL RULES" in item(52)["raw_answer"] and cases["case-052"].expected_outcome=="correct"
 assert [item(n)["answer_format"] for n in (53,54,55)]==["short_text","single_choice","number"]

@pytest.mark.parametrize("bad",["1.0","01","-0","0.0","1.2300","+1"," 1","1e0","NaN","Infinity"])
def test_decimal_requires_exact_canonical_plain_representation(bad):
 raw=json.loads(FIXTURE.read_text());raw["cases"][0]["max_score"]=bad
 with pytest.raises(AcceptanceContractError): GoldenDatasetV2.from_dict(raw)

def test_v1_payload_is_rejected_not_reinterpreted():
 raw=json.loads(FIXTURE.read_text());raw["version"]="checking_golden_dataset_v1"
 with pytest.raises(AcceptanceContractError,match="unsupported_dataset_version"): GoldenDatasetV2.from_dict(raw)
def test_exact_fingerprint_and_all_required_gates():
 d=dataset();r=evaluate_golden_dataset(d,observations(d),AcceptanceThresholdPolicy()); assert r.accepted; assert r.corpus_fingerprint=="35a13a0e35d665676cf9bffcbc13d54922f3dfc9f429f911e64507f2aab2527f"; assert (r.metrics.finding_identity_agreement,r.metrics.maximum_score_agreement,r.metrics.confidence_agreement)==(Decimal("1.0000"),)*3

def test_review_and_confidence_contract_is_exact_and_canonical():
 d=dataset(); obs=observations(d)
 assert all((x.review_reason is not None)==x.review_required for x in obs)
 assert all(x.confidence_reasons==c.expected_confidence_reasons for x,c in zip(obs,d.cases))
 assert all(len(x.confidence_reasons)<=16 and len(set(x.confidence_reasons))==len(x.confidence_reasons) for x in obs)
 assert all(type(c.expected_structured_output_valid) is bool and type(c.expected_provider_failed) is bool for c in d.cases)

@pytest.mark.parametrize('field,value',[('review_required',True),('review_reason','changed'),('confidence_policy','changed'),('confidence',Decimal('0.0000')),('confidence_reasons',('changed',)),('structured_output_valid',False),('provider_failed',True)])
def test_per_case_output_gates_are_fatal(field,value):
 d=dataset();obs=list(observations(d)); from dataclasses import replace; x=obs[0]
 changed=replace(x,**{field:value})
 obs[0]=changed; assert not evaluate_golden_dataset(d,obs,AcceptanceThresholdPolicy()).accepted

def test_maximum_score_mismatch_is_individually_fatal():
 d=dataset();obs=list(observations(d)); from dataclasses import replace
 obs[0]=replace(obs[0],score=Decimal("99"),max_score=Decimal("99"))
 assert not evaluate_golden_dataset(d,obs,AcceptanceThresholdPolicy()).accepted

def test_finding_mismatch_is_individually_fatal():
 d=dataset();obs=list(observations(d)); from dataclasses import replace
 index=44; obs[index]=replace(obs[index],findings=())
 assert not evaluate_golden_dataset(d,obs,AcceptanceThresholdPolicy()).accepted
def test_missing_and_unexpected_are_fatal():
 d=dataset();obs=observations(d); assert not evaluate_golden_dataset(d,obs[:-1],AcceptanceThresholdPolicy()).accepted
 with pytest.raises(AcceptanceContractError): evaluate_golden_dataset(d,obs+(obs[0],),AcceptanceThresholdPolicy())
def test_detached_immutable_input():
 raw=json.loads(FIXTURE.read_text());d=GoldenDatasetV2.from_dict(raw);raw['cases'][0]['input']['snapshot']['items'][0]['raw_answer']='changed';assert d.cases[0].input['snapshot']['items'][0]['raw_answer']!='changed'
 with pytest.raises(TypeError): d.cases[0].input['x']=1
 with pytest.raises(FrozenInstanceError): d.cases[0].case_id='x'
