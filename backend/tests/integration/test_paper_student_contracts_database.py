"""PR5 blockers: real production routes and rollback-isolated PostgreSQL state."""

import json
import os
from uuid import uuid4

import httpx
import pytest
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import event, text

from app.application.remediation import RemediationError
from app.application.remediation_execution import RemediationExecutionService
from app.infrastructure.student_assessment_repository import StudentAssessmentService
from app.presentation import remediation_routes, student_assessment_routes
from app.presentation.remediation_schemas import RemediationPlanResponse
from tests.integration.test_manual_paper_review_database import grade, manual, policies
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_publication_withdrawal_database import withdraw
from tests.integration.test_paper_remediation_database import payload, published
from tests.integration.test_paper_result_publication_database import publish
from tests.integration.test_scan_storage_retention_database import http_app

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]
ROOT = "/api/assessment-core/student/assignments"


def application(c, monkeypatch):
    app, identity = http_app(c)
    app.include_router(remediation_routes.router)
    app.include_router(student_assessment_routes.router)
    monkeypatch.setattr(
        student_assessment_routes, "service", lambda: StudentAssessmentService(c[1])
    )
    monkeypatch.setattr(
        remediation_routes,
        "execution_service",
        lambda: RemediationExecutionService(c[1]),
    )

    @app.exception_handler(RemediationError)
    async def error_handler(request, error):
        return JSONResponse(status_code=error.status, content={"detail": error.code})

    return app, identity


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173", "Idempotency-Key": str(uuid4())},
    )


def student(identity, c, key="student_a"):
    identity.update(role="student", student_id=c[2][key] if key else None)


async def manual_publication(c):
    await policies(c)
    review = await grade(c, await manual(c))
    return review, await publish(c, review.latest_approved_revision_id)


async def test_normal_assignment_navigation_withdraw_republish(
    execution_database, monkeypatch
):
    c = execution_database
    review, a = await manual_publication(c)
    app, identity = application(c, monkeypatch)
    student(identity, c)
    async with client(app) as http:
        assignments = await http.get(ROOT)
        assert assignments.status_code == 200, assignments.text
        assignment = assignments.json()["items"][0]
        assignment_id = assignment["assignment_id"]
        detail = await http.get(f"{ROOT}/{assignment_id}")
        assert detail.status_code == 200
        url = f"{ROOT}/{assignment_id}/paper-result"
        result = await http.get(url)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["id"] == str(a["id"])
        assert data["assignment_id"] == assignment_id
        assert data["assignment_title"] == assignment["title"] == c[2]["suffix"]
        encoded = json.dumps(data)
        for forbidden in (
            "approved_review_revision",
            "check_run",
            "model_run",
            "withdraw",
            "history",
            "storage_reference",
            "participant_id",
        ):
            assert forbidden not in encoded
        page = await http.get(data["pages"][0]["content_url"])
        assert page.status_code == 200
        await withdraw(c, a)
        hidden = await http.get(url)
        assert hidden.status_code == 404 and hidden.json() == {
            "detail": "paper_result_not_found"
        }
        assert (await http.get(data["pages"][0]["content_url"])).status_code == 404
        b = await publish(c, review.latest_approved_revision_id)
        current = await http.get(url)
        assert current.status_code == 200 and current.json()["id"] == str(b["id"])
        assert b["id"] != a["id"] and "history" not in current.text


@pytest.mark.parametrize(
    "target",
    [
        "assignment",
        "paper",
        "page",
        "publication",
        "unlinked",
        "unknown_assignment",
        "source_pdf",
    ],
)
async def test_student_privacy(execution_database, monkeypatch, target):
    c = execution_database
    _, pub = await manual_publication(c)
    app, identity = application(c, monkeypatch)
    student(
        identity,
        c,
        "student_b" if target not in {"unlinked", "source_pdf"} else "student_a",
    )
    paths = {
        "assignment": f"{ROOT}/{c[2]['assignment']}/paper-result",
        "paper": f"/api/assessment-core/student/paper-submissions/{c[2]['paper_a']}/result",
        "page": f"/api/assessment-core/student/paper-submissions/{c[2]['paper_a']}/pages/{c[2]['page_a']}",
        "publication": f"/api/assessment-core/student/paper-submissions/{pub['id']}/result",
        "unlinked": f"{ROOT}/{c[2]['assignment']}/paper-result",
        "unknown_assignment": f"{ROOT}/{uuid4()}/paper-result",
        "source_pdf": f"/api/assessment-core/student/paper-submissions/{c[2]['paper_a']}/pages/{c[2]['source']}",
    }
    if target == "unlinked":
        identity["student_id"] = None
    async with client(app) as http:
        response = await http.get(paths[target])
        assert response.status_code == (403 if target == "unlinked" else 404), (
            response.text
        )


@pytest.mark.parametrize("state", ["unpublished", "approved", "withdrawn"])
async def test_no_active_result(execution_database, monkeypatch, state):
    c = execution_database
    if state != "unpublished":
        await policies(c)
        r = await grade(c, await manual(c))
        if state == "withdrawn":
            await withdraw(c, await publish(c, r.latest_approved_revision_id))
    app, identity = application(c, monkeypatch)
    student(identity, c)
    async with client(app) as http:
        response = await http.get(f"{ROOT}/{c[2]['assignment']}/paper-result")
        assert response.status_code == 404 and response.json() == {
            "detail": "paper_result_not_found"
        }


async def test_separate_assignment_and_bounded_queries(execution_database, monkeypatch):
    c = execution_database
    _, a = await manual_publication(c)
    app, identity = application(c, monkeypatch)
    student(identity, c)
    ids = c[2]
    extra = uuid4()
    await c[0].execute(
        text(
            "INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,max_attempts,created_by) VALUES (:extra,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 day',3,:teacher)"
        ),
        {**ids, "extra": extra},
    )
    await c[0].execute(
        text(
            "INSERT INTO assignment_participants(id,assignment_id,student_id) VALUES (:id,:assignment,:student)"
        ),
        {"id": uuid4(), "assignment": extra, "student": ids["student_a"]},
    )
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(c[0].sync_connection, "before_cursor_execute", capture)
    try:
        async with client(app) as http:
            response = await http.get(f"{ROOT}/{ids['assignment']}/paper-result")
            assert response.status_code == 200 and response.json()["id"] == str(a["id"])
            assert len([s for s in statements if s.startswith("SELECT")]) == 3
            assert (await http.get(f"{ROOT}/{extra}/paper-result")).status_code == 404
    finally:
        event.remove(c[0].sync_connection, "before_cursor_execute", capture)


async def test_three_assignment_results_resolve_independently(
    execution_database, monkeypatch
):
    from tests.integration.test_paper_checking_intake_database import freeze

    c = execution_database
    _, a = await manual_publication(c)
    ids = {**c[2]}
    for name in (
        "assignment",
        "participant_a",
        "batch",
        "batch_artifact",
        "page_a",
        "revision",
        "entry_a",
        "paper_a",
    ):
        ids[name] = uuid4()
    ids["suffix"] = uuid4().hex
    for sql in (
        "INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,max_attempts,created_by) VALUES (:assignment,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 day',3,:teacher)",
        "INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id,variant_assigned_at) VALUES (:participant_a,:assignment,:student_a,:variant_a,clock_timestamp())",
        "INSERT INTO assessment_scan_batches(id,assignment_id,class_group_id,created_by_user_id,status,instruction_text,matching_revision,row_version,request_key,request_hash) VALUES (:batch,:assignment,:group,:teacher,'ready_for_checking','Mark exactly',1,1,:suffix,:source_hash)",
        "INSERT INTO scan_batch_artifacts(id,batch_id,input_artifact_id,upload_position,original_filename,extraction_status,page_count) VALUES (:batch_artifact,:batch,:source,0,'private.pdf','completed',1)",
        "INSERT INTO scan_pages(id,batch_artifact_id,source_page_index,derived_render_artifact_id,width_px,height_px,rotation_degrees,coordinate_space_version,content_fingerprint,status) VALUES (:page_a,:batch_artifact,0,:render_a,1,1,0,'normalized_upright_v1',:hash_a,'ready')",
        "INSERT INTO scan_grouping_revisions(id,batch_id,revision,based_on_matching_revision,created_by_user_id,status,confirmed_at,confirmed_by_user_id) VALUES (:revision,:batch,1,1,:teacher,'confirmed',clock_timestamp(),:teacher)",
        "INSERT INTO scan_grouping_entries(id,grouping_revision_id,assignment_participant_id,position) VALUES (:entry_a,:revision,:participant_a,0)",
        "INSERT INTO scan_grouping_pages(grouping_entry_id,grouping_revision_id,scan_page_id,page_order) VALUES (:entry_a,:revision,:page_a,0)",
        "INSERT INTO paper_submissions(id,batch_id,grouping_revision_id,assignment_id,assignment_participant_id,student_id,assigned_variant_id,status,created_by_user_id) VALUES (:paper_a,:batch,:revision,:assignment,:participant_a,:student_a,:variant_a,'ready_for_checking',:teacher)",
        "INSERT INTO paper_submission_pages(paper_submission_id,scan_page_id,page_order) VALUES (:paper_a,:page_a,0)",
    ):
        await c[0].execute(text(sql), ids)
    other = (c[0], c[1], ids, c[3])
    await freeze(c[1], ids)
    review = await grade(other, await manual(other))
    z = await publish(other, review.latest_approved_revision_id)
    y = uuid4()
    await c[0].execute(
        text(
            "INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,max_attempts,created_by) VALUES (:y,:assessment,:group,clock_timestamp(),clock_timestamp()+interval '1 day',3,:teacher)"
        ),
        {**ids, "y": y},
    )
    await c[0].execute(
        text(
            "INSERT INTO assignment_participants(id,assignment_id,student_id) VALUES (:p,:y,:student_a)"
        ),
        {**ids, "y": y, "p": uuid4()},
    )
    app, identity = application(c, monkeypatch)
    student(identity, c)
    async with client(app) as http:
        for assignment, publication in (
            (c[2]["assignment"], a),
            (ids["assignment"], z),
        ):
            result = await http.get(f"{ROOT}/{assignment}/paper-result")
            assert result.status_code == 200, result.text
            assert result.json()["assignment_id"] == str(assignment)
            assert result.json()["id"] == str(publication["id"])
        assert (await http.get(f"{ROOT}/{y}/paper-result")).status_code == 404


async def test_generated_openapi_contracts(execution_database, monkeypatch):
    app, _ = application(execution_database, monkeypatch)
    schema = app.openapi()
    field = schema["components"]["schemas"]["RemediationPlanResponse"]["properties"][
        "source_check_run_id"
    ]
    assert {"type": "null"} in field["anyOf"]
    assert {"type": "string", "format": "uuid"} in field["anyOf"]
    route = schema["paths"][ROOT + "/{assignment_id}/paper-result"]["get"]
    assert route["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/StudentAssignmentPaperResult")


async def test_digital_missing_run_rejected_by_database(execution_database):
    from tests.integration.c9ab_fixtures import seed_execution_world
    from tests.integration.test_manual_paper_review_database import rejected

    c = execution_database
    ids = await seed_execution_world(c[0])
    await rejected(
        c[0],
        "UPDATE remediation_plans SET source_check_run_id=NULL WHERE id=:id",
        {"id": ids["plan_0"]},
    )


@pytest.mark.parametrize("source", ["manual", "ai"])
async def test_remediation_http_lifecycle_and_replacement(
    execution_database, monkeypatch, source
):
    c = execution_database
    if source == "manual":
        review, pub = await manual_publication(c)
        run = None
    else:
        run, review, pub, _ = await published(c)
    app, identity = application(c, monkeypatch)
    async with client(app) as http:
        body = payload(
            c,
            pub,
            items=[
                {"task_version_id": c[2]["version_b"], "selection_source": "manual"}
            ],
        ).model_dump(mode="json")
        create_url = (
            f"/api/assessment-core/paper-submissions/{c[2]['paper_a']}/remediations"
        )
        response = await http.post(create_url, json=body)
        assert response.status_code == 201, response.text
        plan = response.json()
        assert plan["source_check_run_id"] == (str(run.id) if run else None)
        assert plan["source_type"] == "paper" and plan[
            "source_paper_publication_id"
        ] == str(pub["id"])
        url = f"/api/remediations/{plan['id']}"
        assert (await http.get(url)).status_code == 200
        stale_body = {**body, "creation_key": "stale-draft"}
        stale = await http.post(create_url, json=stale_body)
        assert stale.status_code == 201
        assigned = await http.post(url + "/assign", json={})
        assert assigned.status_code == 200, assigned.text
        history = await http.get(
            f"/api/classrooms/{c[2]['group']}/students/{c[2]['student_a']}/remediations"
        )
        assert history.status_code == 200, history.text
        await withdraw(c, pub)
        student(identity, c)
        student_url = f"/api/student/remediations/{plan['id']}"
        for endpoint in (
            "/api/student/remediations",
            student_url,
            student_url + "/execution",
        ):
            result = await http.get(endpoint)
            assert result.status_code == 200, result.text
        b = await publish(c, review.latest_approved_revision_id)
        assert b["id"] != pub["id"]
        result = await http.post(student_url + "/start", json={})
        assert result.status_code == 201, result.text
        assert (await http.get(student_url + "/execution")).status_code == 200
        student(identity, c, "student_b")
        assert (await http.get(student_url)).status_code == 404
        assert (await http.post(student_url + "/start", json={})).status_code == 404
        identity.update(role="teacher", student_id=None)
        old = await http.get(url)
        assert old.status_code == 200 and old.json()[
            "source_paper_publication_id"
        ] == str(pub["id"])
        stale_assign = await http.post(
            f"/api/remediations/{stale.json()['id']}/assign", json={}
        )
        assert (
            stale_assign.status_code == 409 and "publication_stale" in stale_assign.text
        )
        assert (await http.get(url + "/result")).status_code == 200
        invalid_digital = {
            **plan,
            "source_type": "digital",
            "source_check_run_id": None,
        }
        with pytest.raises(ValidationError, match="digital remediation requires"):
            RemediationPlanResponse.model_validate(invalid_digital)
        if source == "manual":
            for table in ("check_runs", "model_runs", "paper_ai_revisions"):
                assert await c[0].scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.parametrize("role", ["co_teacher", "unrelated", "revoked"])
async def test_manual_teacher_authorization(execution_database, monkeypatch, role):
    c = execution_database
    _, pub = await manual_publication(c)
    user = uuid4()
    await c[0].execute(
        text(
            "INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:id,:login,:login,'Co teacher','hash')"
        ),
        {"id": user, "login": user.hex},
    )
    if role != "unrelated":
        await c[0].execute(
            text(
                "INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group,:user,:teacher)"
            ),
            {**c[2], "user": user},
        )
    app, identity = application(c, monkeypatch)
    identity["user_id"] = user
    async with client(app) as http:
        base = f"/api/assessment-core/paper-submissions/{c[2]['paper_a']}"
        expected = 404 if role == "unrelated" else 200
        assert (
            await http.get(base + "/remediation-eligibility")
        ).status_code == expected
        response = await http.post(
            base + "/remediations",
            json=payload(
                c,
                pub,
                items=[
                    {"task_version_id": c[2]["version_b"], "selection_source": "manual"}
                ],
            ).model_dump(mode="json"),
        )
        assert response.status_code == (404 if role == "unrelated" else 201), (
            response.text
        )
        if role == "unrelated":
            return
        if role == "revoked":
            await c[0].execute(
                text("DELETE FROM class_group_teachers WHERE teacher_user_id=:user"),
                {"user": user},
            )
        result = await http.post(
            f"/api/remediations/{response.json()['id']}/assign", json={}
        )
        assert result.status_code == (404 if role == "revoked" else 200), result.text
