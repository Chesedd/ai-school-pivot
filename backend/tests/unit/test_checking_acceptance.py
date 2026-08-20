import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from app.application.checking_acceptance import (
    ACCEPTANCE_REPORT_VERSION, ACCEPTANCE_THRESHOLDS_VERSION, GOLDEN_DATASET_VERSION,
    AcceptanceContractError, AcceptanceThresholdPolicy, GoldenDatasetV1,
    ObservedCheckingResultV1, evaluate_golden_dataset,
)

FIXTURE=Path(__file__).parents[1]/"fixtures"/"checking_golden_v1.json"
CORPUS_FINGERPRINT="47782edb9be6bd6bebb31bd553014c74b1d6c146a1bce129ba986618d3561003"

def dataset(): return GoldenDatasetV1.from_dict(json.loads(FIXTURE.read_text()))
def observations(ds=None):
    ds=ds or dataset()
    return tuple(ObservedCheckingResultV1(x.case_id,x.expected_checker,x.expected_outcome,x.expected_reason,x.expected_score,x.max_score,x.expected_review,x.expected_findings) for x in ds.cases)

def test_all_60_cases_distribution_and_versions():
    ds=dataset(); assert len(ds.cases)==60
    assert {x.category:sum(y.category==x.category for y in ds.cases) for x in ds.cases}=={"exact_text":8,"choice":12,"numeric":10,"structured_expression":10,"llm_rubric":12,"boundary":8}
    assert ds.version==GOLDEN_DATASET_VERSION
    assert AcceptanceThresholdPolicy().version==ACCEPTANCE_THRESHOLDS_VERSION

def test_exact_fingerprints_metrics_and_serialization():
    report=evaluate_golden_dataset(dataset(),observations(),AcceptanceThresholdPolicy())
    assert report.corpus_fingerprint==CORPUS_FINGERPRINT
    assert report.observed_fingerprint=="29e6050487aa015441df61c4631af9bf109b6e385f4623c2149103d7e1e260f8"
    assert report.report_fingerprint=="f9109adb6f4971692a9184a34a197767db999145c23d9aba7d7acd0cfff95165"
    assert report.version==ACCEPTANCE_REPORT_VERSION and report.accepted
    assert report.to_json()==report.to_json() and report.metrics.total_cases==report.metrics.evaluated_cases==60
    assert report.metrics.score_mae==Decimal(0) and report.metrics.required_review_recall==Decimal("1.0000")

@pytest.mark.parametrize("field,value",[("case_id","BAD ID"),("case_id",True),("max_score",1.0),("max_score",True),("max_score","1.0"),("expected_score","NaN")])
def test_malformed_identity_and_decimal_are_privacy_safe(field,value):
    raw=json.loads(FIXTURE.read_text()); raw["cases"][0][field]=value
    with pytest.raises(AcceptanceContractError) as caught: GoldenDatasetV1.from_dict(raw)
    assert len(str(caught.value))<=64 and "synthetic" not in str(caught.value)

def test_strict_schemas_versions_duplicate_and_order():
    raw=json.loads(FIXTURE.read_text()); raw["extra"]=1
    with pytest.raises(AcceptanceContractError): GoldenDatasetV1.from_dict(raw)
    raw=json.loads(FIXTURE.read_text()); raw["version"]="v2"
    with pytest.raises(AcceptanceContractError): GoldenDatasetV1.from_dict(raw)
    raw=json.loads(FIXTURE.read_text()); raw["cases"][1]["case_id"]=raw["cases"][0]["case_id"]
    with pytest.raises(AcceptanceContractError): GoldenDatasetV1.from_dict(raw)

def test_missing_unexpected_and_duplicate_observations():
    ds=dataset(); obs=observations(ds)
    report=evaluate_golden_dataset(ds,obs[:-1]+(ObservedCheckingResultV1("unexpected",obs[0].checker,obs[0].outcome,obs[0].reason,obs[0].score,obs[0].max_score,obs[0].review_required),),AcceptanceThresholdPolicy())
    assert (report.metrics.missing_results,report.metrics.unexpected_results,report.accepted)==(1,1,False)
    with pytest.raises(AcceptanceContractError): evaluate_golden_dataset(ds,obs+(obs[0],),AcceptanceThresholdPolicy())

def test_changed_result_invalidates_observed_and_report_fingerprint():
    ds=dataset(); base=evaluate_golden_dataset(ds,observations(ds),AcceptanceThresholdPolicy())
    obs=list(observations(ds)); x=obs[0]
    obs[0]=ObservedCheckingResultV1(x.case_id,x.checker,"incorrect","exact_mismatch",Decimal(0),x.max_score,x.review_required)
    changed=evaluate_golden_dataset(ds,obs,AcceptanceThresholdPolicy())
    assert changed.observed_fingerprint!=base.observed_fingerprint and changed.report_fingerprint!=base.report_fingerprint and not changed.accepted

def test_threshold_equality_zero_unsafe_and_review_recall():
    report=evaluate_golden_dataset(dataset(),observations(),AcceptanceThresholdPolicy())
    assert report.metrics.unsafe_auto_score_count==0 and report.metrics.unsafe_auto_score_rate==Decimal("0.0000")
    assert report.metrics.required_review_recall==Decimal("1.0000") and report.accepted

def test_source_nonmutation_and_detached_immutability():
    raw=json.loads(FIXTURE.read_text()); before=json.dumps(raw,sort_keys=True); ds=GoldenDatasetV1.from_dict(raw)
    raw["cases"][2]["metadata"]["scenario"]="changed"
    assert ds.cases[2].metadata["scenario"]!="changed" and json.dumps(json.loads(FIXTURE.read_text()),sort_keys=True)==before
    with pytest.raises(TypeError): ds.cases[0].metadata["x"]=1
    with pytest.raises(FrozenInstanceError): ds.cases[0].case_id="x"

def test_outcome_score_and_finding_invariants():
    raw=json.loads(FIXTURE.read_text()); raw["cases"][0]["expected_score"]="0"
    with pytest.raises(AcceptanceContractError): GoldenDatasetV1.from_dict(raw)
    raw=json.loads(FIXTURE.read_text()); raw["cases"][0]["expected_findings"]=[{"finding_type":"x"},{"finding_type":"x"}]
    with pytest.raises(AcceptanceContractError): GoldenDatasetV1.from_dict(raw)
