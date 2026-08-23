from types import SimpleNamespace
from uuid import uuid4

from app.application.authoring_quality import calculate_quality_report


REQUEST = {"schema_version":"authoring-request.v1","task_goal":"Practice addition",
    "subject":"math","grade":"g5","topic":"addition","subtopic":"whole-numbers",
    "task_type":"calculation","answer_format":"number","difficulty":20,"skills":["add"],
    "pedagogical_constraints":[],"source_text":None,"language":"en","policy_version":"authoring-v1"}
DRAFT = {"schema_version":"authoring_review_draft.v1","title":"Add","statement":"2 + 2?",
    "task_type":"calculation","answer_format":"number","choice_options":[],"expected_answer":"4",
    "solution":"Add the numbers.","hints":["Count."]}
SOLVER = {"schema_version":"solver_result.v1","status":"solvable","proposed_answer":"4",
    "reasoning_summary":"Addition gives four."}


def values(draft=None, request=None, solver=None):
    session = SimpleNamespace(id=uuid4(), frozen_request=request or REQUEST,
        request_fingerprint="a" * 64, solver_result=solver or SOLVER)
    review = SimpleNamespace(id=uuid4(), version=2, draft=draft or DRAFT)
    return session, review


def test_quality_report_is_deterministic_bounded_and_has_no_task_or_provider_output():
    session, review = values()
    first = calculate_quality_report(session, review)
    second = calculate_quality_report(session, review)
    assert first == second
    assert first.overall_status == "passed"
    encoded = first.model_dump_json()
    assert "2 + 2" not in encoded and "reasoning_summary" not in encoded
    assert next(x for x in first.checks if x.code == "duplicate_detection").evidence.metadata == {
        "provider":"not_available"}


def test_answer_mismatch_is_warning_not_blocking():
    session, review = values(draft={**DRAFT, "expected_answer":"5"})
    report = calculate_quality_report(session, review)
    answer = next(x for x in report.checks if x.code == "answer_consistency")
    assert (answer.severity, answer.passed, answer.evidence.metadata["comparison"]) == (
        "warning", False, "mismatch")
    assert report.overall_status == "warnings"


def test_incomplete_or_structurally_invalid_content_blocks():
    session, review = values(draft={**DRAFT, "title":""})
    report = calculate_quality_report(session, review)
    assert report.overall_status == "blocked"
    failed = {x.code for x in report.checks if not x.passed and x.severity == "blocking"}
    assert {"completeness", "bounds", "valid_structure"} <= failed


def test_catalog_consistency_protects_frozen_request():
    session, review = values(draft={**DRAFT, "answer_format":"short_text"})
    report = calculate_quality_report(session, review)
    catalog = next(x for x in report.checks if x.code == "catalog_consistency")
    assert not catalog.passed and catalog.severity == "blocking"
    assert catalog.evidence.metadata["task_type_matches"] is True
    assert catalog.evidence.metadata["answer_format_matches"] is False
