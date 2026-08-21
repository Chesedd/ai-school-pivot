import asyncio,json
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
def test_required_semantic_variant_matrix_is_explicit():
 d=dataset(); actual={category:{case.metadata["scenario"] for case in d.cases if case.category==category} for category in {"choice","numeric","structured_expression","llm_rubric","boundary"}}
 assert actual["choice"]=={"single_correct","single_mismatch","single_unknown","multiple_exact_set","multiple_mismatched_set","multiple_order_independent","accepted_or_first","accepted_or_second","all_or_nothing","weighted_correct","weighted_partial","invalid_selection"}
 assert {"exact","tolerance_boundary","tolerance_inside","tolerance_outside","negative","zero","decimal_precision","invalid_methodology"}<=actual["numeric"]
 assert {"identity","whitespace_sensitive","reordered","unanswered","equivalence_unproven","malformed_methodology"}<=actual["structured_expression"]
 assert {"correct","partially_correct","incorrect","required_criterion_missed","typical_error_provenance","skill_rubric_provenance","missing_coverage","duplicate_coverage","invalid_coverage_order_or_evidence","provider_failure","malformed_structured_or_solution_leak","prompt_injection_as_data"}==actual["llm_rubric"]
 assert {"unanswered_exact","unanswered_choice","unanswered_numeric","insufficient_missing_rubric","insufficient_empty_rubric","insufficient_coverage","manual_capability","manual_expression_capability"}==actual["boundary"]

@pytest.mark.parametrize("bad",["1.0","01","-0","0.0","1.2300","+1"," 1","1e0","NaN","Infinity"])
def test_decimal_requires_exact_canonical_plain_representation(bad):
 raw=json.loads(FIXTURE.read_text());raw["cases"][0]["max_score"]=bad
 with pytest.raises(AcceptanceContractError): GoldenDatasetV2.from_dict(raw)

def test_v1_payload_is_rejected_not_reinterpreted():
 raw=json.loads(FIXTURE.read_text());raw["version"]="checking_golden_dataset_v1"
 with pytest.raises(AcceptanceContractError,match="unsupported_dataset_version"): GoldenDatasetV2.from_dict(raw)
def test_exact_fingerprint_and_all_required_gates():
 d=dataset();r=evaluate_golden_dataset(d,observations(d),AcceptanceThresholdPolicy()); assert r.accepted; assert r.corpus_fingerprint=="c8222d2bc0152e6d44edf37fd1db5a0ac2f7bcc317c211ca4a468f70c2aeae1c"; assert (r.metrics.finding_identity_agreement,r.metrics.maximum_score_agreement,r.metrics.confidence_agreement)==(Decimal("1.0000"),)*3
@pytest.mark.parametrize('field,value',[('confidence',Decimal('0.0000')),('structured_output_valid',False),('provider_failed',True)])
def test_per_case_output_gates_are_fatal(field,value):
 d=dataset();obs=list(observations(d)); from dataclasses import replace; x=obs[0]
 changed=replace(x,**{field:value})
 obs[0]=changed; assert not evaluate_golden_dataset(d,obs,AcceptanceThresholdPolicy()).accepted
def test_missing_and_unexpected_are_fatal():
 d=dataset();obs=observations(d); assert not evaluate_golden_dataset(d,obs[:-1],AcceptanceThresholdPolicy()).accepted
 with pytest.raises(AcceptanceContractError): evaluate_golden_dataset(d,obs+(obs[0],),AcceptanceThresholdPolicy())
def test_detached_immutable_input():
 raw=json.loads(FIXTURE.read_text());d=GoldenDatasetV2.from_dict(raw);raw['cases'][0]['input']['snapshot']['items'][0]['raw_answer']='changed';assert d.cases[0].input['snapshot']['items'][0]['raw_answer']!='changed'
 with pytest.raises(TypeError): d.cases[0].input['x']=1
 with pytest.raises(FrozenInstanceError): d.cases[0].case_id='x'
