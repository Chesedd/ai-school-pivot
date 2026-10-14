"""PR5 contract-only reproduction. Run from backend with the repository venv.

No database connection, application lifespan, mutation, or provider call is used.
The isolated HTTP route demonstrates the production response model's behavior;
it is not a real-backend/PostgreSQL end-to-end test.
"""

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.application.paper_result_publication import PublicationSnapshot
from app.infrastructure.paper_result_publication_repository import (
    PaperResultPublicationRepository,
)
from app.presentation.remediation_schemas import RemediationPlanResponse
from app.presentation.student_assessment_schemas import StudentAssignmentDetail


def main():
    uid, now = uuid4(), datetime.now(timezone.utc)
    plan = dict(
        id=uid, student_id=uid, class_group_id=uid, source_assignment_id=uid,
        source_assignment_participant_id=uid, source_submission_id=None,
        source_check_run_id=None, source_type="paper", source_paper_submission_id=uid,
        source_paper_publication_id=uid, source_assessment_item_ids=[uid],
        publication_finding_ids=[], status="draft", title="Manual paper remediation",
        instructions=None, due_at=None, review_acknowledged_at=None,
        created_at=now, updated_at=now, assigned_at=None, cancelled_at=None,
        finding_ids=[], items=[],
    )
    try:
        RemediationPlanResponse.model_validate(plan)
        raise AssertionError("Expected manual-source response validation failure")
    except ValidationError as error:
        errors = [(list(e["loc"]), e["type"]) for e in error.errors()]
        assert errors == [(["source_check_run_id"], "uuid_type")], errors
        print("MANUAL_DTO:", errors)

    isolated = FastAPI()

    @isolated.post("/proof/manual-remediation", response_model=RemediationPlanResponse)
    def proof():
        return plan

    with TestClient(isolated, raise_server_exceptions=False) as client:
        response = client.post("/proof/manual-remediation")
    assert response.status_code == 500
    print("ISOLATED_HTTP_SAME_RESPONSE_MODEL:", response.status_code)
    plan["source_check_run_id"] = uid
    assert RemediationPlanResponse.model_validate(plan).source_check_run_id == uid
    print("NON_NULL_CHECK_RUN_CONTROL: valid")

    snapshot = dict(
        paper_submission_id=str(uid), approved_review_revision_id=str(uid),
        summary="Published comment",
        items=[dict(assessment_item_id=str(uid), status="partially_correct",
                    accepted_score="1", max_score="2")], findings=[],
        grade=dict(calculated_total_score="1", total_max_score="2",
                   calculated_percentage="50", calculated_grade="3", final_grade="3"),
    )
    validated = PublicationSnapshot.model_validate_json(json.dumps(snapshot))
    row = SimpleNamespace(
        id=uid, paper_submission_id=uid, publication_number=1,
        approved_review_revision_id=uid, created_at=now,
        publication_snapshot=validated.model_dump(mode="json"),
    )
    projection = PaperResultPublicationRepository.project(row, True)
    assert "assignment_id" not in projection and "assignment_id" not in projection["result"]
    assert "title" not in projection and "title" not in projection["result"]
    assert not any("paper" in field for field in StudentAssignmentDetail.model_fields)
    print("STUDENT_PUBLICATION_FIELDS:", sorted(projection))
    print("STUDENT_SNAPSHOT_FIELDS:", sorted(projection["result"]))
    print("STUDENT_ASSIGNMENT_FIELDS:", sorted(StudentAssignmentDetail.model_fields))
    print("ASSIGNMENT_DISCOVERY_LINK: absent in both contracts")

    # Engine construction only; this non-routable placeholder is never connected.
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://probe:probe@127.0.0.1:1/probe")
    from app.main import app

    schema = app.openapi()
    check_run = schema["components"]["schemas"]["RemediationPlanResponse"]["properties"]["source_check_run_id"]
    assert check_run["type"] == "string" and "anyOf" not in check_run
    print("OPENAPI_CHECK_RUN_SCHEMA:", check_run)
    for path, methods in schema["paths"].items():
        if "/student/" in path and any(s in path for s in ("paper", "assignment", "remediation")):
            print("ROUTE", ",".join(methods), path)
    print("PASS: both blocking contract mismatches reproduced")


if __name__ == "__main__":
    main()
