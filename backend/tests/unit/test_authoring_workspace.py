from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.application.authoring_workspace import workspace_view
from app.infrastructure.authoring_repository import AuthoringWorkspaceRecord


def test_workspace_dto_derives_statuses_and_excludes_sensitive_fields():
    session = SimpleNamespace(id=uuid4(), owner_id=uuid4(), status="ready",
        created_at=datetime.now(timezone.utc), generator_route={"provider_id":"safe","model_id":"v1"},
        generated_draft={"statement":"must not leak"}, solver_result={"status":"solvable",
            "proposed_answer":"secret", "reasoning_summary":"hidden reasoning"},
        validation_result={"schema_version":"task_validation_result.v1","status":"validated",
            "comparator":"exact_text_v1"}, semantic_status="validated")
    attempt = SimpleNamespace(role="generator", status="succeeded", provider_id="safe", model_id="v1",
        cost_amount=Decimal("0.25"), currency="USD", input_tokens=10, output_tokens=5,
        cache_read_tokens=2, cache_write_tokens=1, prompt_snapshot={"prompt":"secret"},
        settings_snapshot={"api_key":"secret"}, provider_request_id="raw-id")

    view = workspace_view(AuthoringWorkspaceRecord(session, (attempt,), None, (), (), None))
    payload = view.model_dump(mode="json")

    assert payload["session"]["lifecycle_status"] == "ready"
    assert payload["generation"]["generator_attempt_status"] == "succeeded"
    assert payload["solver"] == {"solver_status":"solvable", "validation_status":"validated",
        "semantic_result":{"schema_version":"task_validation_result.v1","status":"validated",
            "comparator":"exact_text_v1"}}
    assert payload["quality"]["quality_status"] == "not_available"
    assert payload["diagnostics"]["total_cost_by_currency"] == [{"currency":"USD","amount":"0.25"}]
    serialized = view.model_dump_json()
    for forbidden in ("must not leak", "secret", "hidden reasoning", "raw-id", "prompt_snapshot",
                      "settings_snapshot", "proposed_answer", "reasoning_summary"):
        assert forbidden not in serialized


def test_workspace_review_and_acceptance_status_derivation():
    owner, revision_id = uuid4(), uuid4()
    session = SimpleNamespace(id=uuid4(), owner_id=owner, status="confirmed",
        created_at=datetime.now(timezone.utc), generator_route=None, generated_draft=None,
        solver_result={"schema_version":"solver_result.v1","status":"solvable",
            "proposed_answer":"42","reasoning_summary":"Checked."},
        validation_result={"schema_version":"task_validation_result.v1","status":"validated",
            "comparator":"exact_text_v1"}, semantic_status="validated",
        request_fingerprint="a"*64,
        frozen_request={"schema_version":"authoring-request.v1","task_goal":"Test",
            "subject":"math","grade":"g7","topic":"numbers","subtopic":None,
            "task_type":"calculation","answer_format":"number","difficulty":50,
            "skills":["reasoning"],"pedagogical_constraints":[],"source_text":None,
            "language":None,"policy_version":"authoring-v1"})
    draft={"title":"Multiply","statement":"What is six times seven?","task_type":"calculation",
        "answer_format":"number","choice_options":[],"expected_answer":"42",
        "solution":"Six times seven is 42.","hints":[]}
    review = SimpleNamespace(id=uuid4(), state="accepted", accepted_revision_id=revision_id,
        version=4, draft=draft)
    revision = SimpleNamespace(id=revision_id, revision_number=3,
        change_summary={"changed_fields":["title"]})
    promotion = SimpleNamespace(task_id=uuid4(), task_version_id=uuid4())
    record = AuthoringWorkspaceRecord(session, (), review, (revision,), (), promotion)
    view = workspace_view(record)
    assert view.review.current_revision == 3
    assert view.review.revision_count == 1
    assert view.review.reviewer_state == "accepted"
    assert view.acceptance.accepted_revision.revision_number == 3
    assert view.acceptance.promoted_task_reference.task_id == promotion.task_id
