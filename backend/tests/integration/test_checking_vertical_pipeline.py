"""Database-free executable vertical acceptance through production Checking."""
import json
from pathlib import Path
import pytest
from app.application.checking_acceptance import AcceptanceThresholdPolicy, GoldenDatasetV1, execute_golden_case, evaluate_golden_dataset
FIXTURE=Path(__file__).parents[1]/"fixtures"/"checking_golden_v1.json"
@pytest.mark.asyncio
async def test_all_golden_inputs_execute_the_production_composition():
    dataset=GoldenDatasetV1.from_dict(json.loads(FIXTURE.read_text()))
    observed=tuple([await execute_golden_case(case.input,case.case_id) for case in dataset.cases])
    report=evaluate_golden_dataset(dataset,tuple(reversed(observed)),AcceptanceThresholdPolicy())
    assert report.accepted
    assert report.metrics.finding_identity_agreement==report.metrics.maximum_score_agreement==report.metrics.confidence_agreement==1
    assert report.metrics.unsafe_auto_score_count==report.metrics.privacy_violation_count==0
@pytest.mark.asyncio
async def test_executor_accepts_input_only_not_expected_fields():
    case=GoldenDatasetV1.from_dict(json.loads(FIXTURE.read_text())).cases[0]
    observation=await execute_golden_case(case.input,case.case_id)
    assert observation.checker=="exact" and observation.case_id==case.case_id
