"""Real teacher HTTP routes, capability and class access acceptance."""

import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text

from app.application.capabilities import capabilities_for_roles
from app.application.principal import Principal
from app.db.session import get_session
from app.presentation.auth_dependencies import require_principal
from app.presentation.scan_intake_routes import router
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_review_database import completed

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]


@pytest_asyncio.fixture
async def review_http(execution_database):
    _, factory, ids, _ = execution_database
    run = await completed(execution_database)
    identity = {"user_id": ids["teacher"], "roles": frozenset({"teacher"})}
    app = FastAPI()
    app.include_router(router)

    async def session():
        async with factory() as db:
            yield db

    def principal():
        return Principal(
            identity["user_id"],
            "teacher",
            "Teacher",
            identity["roles"],
            capabilities_for_roles(identity["roles"]),
            None,
        )

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[require_principal] = principal
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        path = f"/api/assessment-core/paper-submissions/{ids['paper_a']}/check-runs/{run.id}/review"
        yield client, path, identity, execution_database


async def test_authorized_teacher_full_http_workflow(review_http):
    client, path, _, _ = review_http
    started = await client.post(path)
    assert started.status_code == 200
    initial = started.json()
    assert (await client.get(path)).json() == initial
    assert (await client.post(path)).json() == initial
    value = initial["draft"]
    value["summary"] = "Teacher revised through HTTP"
    edited = await client.put(path, json={"expected_version": 1, "draft": value})
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2
    approved = await client.post(path + "/approve", json={"expected_version": 2})
    assert approved.status_code == 200, approved.text
    data = approved.json()
    assert data["state"] == "approved" and data["version"] == 3
    assert (
        data["latest_approved_revision"]["grade_result"]["calculated_percentage"]
        == "100.0000"
    )
    history = (await client.get(path + "/history")).json()["revisions"]
    assert [x["revision_number"] for x in history] == [0, 1, 2]
    assert all(
        x not in str(data)
        for x in (
            "storage_reference",
            "password_hash",
            "validated_response",
            "original_filename",
        )
    )


@pytest.mark.parametrize("role", ["teacher", "student"])
@pytest.mark.parametrize(
    "method,suffix",
    [
        ("POST", ""),
        ("GET", ""),
        ("PUT", ""),
        ("GET", "/history"),
        ("POST", "/approve"),
    ],
)
async def test_unrelated_teacher_and_student_denied_all_routes(
    review_http, role, method, suffix
):
    client, path, identity, _ = review_http
    seeded = (await client.post(path)).json()
    identity.update(user_id=uuid4(), roles=frozenset({role}))
    payload = (
        {"expected_version": 1, "draft": seeded["draft"]}
        if method == "PUT"
        else {"expected_version": 1}
    )
    response = await client.request(
        method, path + suffix, json=payload if method != "GET" else None
    )
    assert response.status_code == (404 if role == "teacher" else 403), response.text


async def test_any_current_class_teacher_can_review(review_http):
    client, path, identity, context = review_http
    connection, _, ids, _ = context
    other = uuid4()
    suffix = other.hex
    await connection.execute(
        text(
            "INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:id,:login,:login,'Other teacher','hash')"
        ),
        {"id": other, "login": suffix},
    )
    await connection.execute(
        text(
            "INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group,:id,:actor)"
        ),
        {"group": ids["group"], "id": other, "actor": ids["teacher"]},
    )
    await client.post(path)
    identity["user_id"] = other
    approved = await client.post(path + "/approve", json={"expected_version": 1})
    assert approved.status_code == 200
    assert approved.json()["latest_approved_revision"]["actor_user_id"] == str(other)


async def test_http_conflicts_mismatched_paper_and_missing_ai(review_http):
    client, path, _, context = review_http
    initial = (await client.post(path)).json()
    await client.post(path + "/approve", json={"expected_version": 1})
    stale_edit = await client.put(
        path, json={"expected_version": 1, "draft": initial["draft"]}
    )
    stale_approval = await client.post(path + "/approve", json={"expected_version": 1})
    for response in (stale_edit, stale_approval):
        assert (
            response.status_code == 409
            and response.json()["detail"] == "paper_review_version_conflict"
        )
    mismatch = path.replace(str(context[2]["paper_a"]), str(context[2]["paper_b"]))
    assert (await client.post(mismatch)).status_code == 404
    from tests.integration.test_paper_checking_execution_database import start

    pending = await start(context, "b")
    pending_path = f"/api/assessment-core/paper-submissions/{context[2]['paper_b']}/check-runs/{pending.id}/review"
    assert (await client.post(pending_path)).status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_version": 1, "calculated_grade": "A"},
        {
            "expected_version": 1,
            "grade_override": {"override_grade": "A", "reason": " "},
        },
        {"expected_version": 0},
    ],
)
async def test_invalid_approval_body_rejected(review_http, payload):
    client, path, _, _ = review_http
    await client.post(path)
    assert (await client.post(path + "/approve", json=payload)).status_code == 422
