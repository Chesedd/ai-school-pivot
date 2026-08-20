"""Database-free vertical acceptance boundary using production acceptance DTOs."""
import json
from pathlib import Path
from app.application.checking_acceptance import AcceptanceThresholdPolicy, GoldenDatasetV1, ObservedCheckingResultV1, evaluate_golden_dataset

FIXTURE=Path(__file__).parents[1]/"fixtures"/"checking_golden_v1.json"

def test_representative_checker_results_compose_deterministically_without_provider_selection():
    dataset=GoldenDatasetV1.from_dict(json.loads(FIXTURE.read_text()))
    selected=[]
    for checker in ("exact","multiple_choice","numeric","structured_expression","llm_rubric","manual_required"):
        case=next(x for x in dataset.cases if x.expected_checker==checker)
        selected.append(ObservedCheckingResultV1(case.case_id,case.expected_checker,case.expected_outcome,case.expected_reason,case.expected_score,case.max_score,case.expected_review,case.expected_findings))
    subset=GoldenDatasetV1(tuple(sorted((next(x for x in dataset.cases if x.case_id==o.case_id) for o in selected),key=lambda x:x.case_id)))
    first=evaluate_golden_dataset(subset,tuple(reversed(selected)),AcceptanceThresholdPolicy())
    second=evaluate_golden_dataset(subset,selected,AcceptanceThresholdPolicy())
    assert first.to_json()==second.to_json() and first.accepted
    assert first.metrics.privacy_violation_count==0

def test_malformed_provider_candidate_is_safe_unclear_review_and_contains_no_content():
    dataset=GoldenDatasetV1.from_dict(json.loads(FIXTURE.read_text()))
    case=next(x for x in dataset.cases if x.expected_reason=="llm_structured_output_invalid")
    observed=ObservedCheckingResultV1(case.case_id,case.expected_checker,case.expected_outcome,case.expected_reason,case.expected_score,case.max_score,case.expected_review)
    subset=GoldenDatasetV1((case,)); report=evaluate_golden_dataset(subset,(observed,),AcceptanceThresholdPolicy())
    assert observed.outcome=="unclear" and observed.review_required and report.accepted
    assert "provider_output" not in report.to_json() and "answer" not in report.to_json()
