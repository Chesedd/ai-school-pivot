"""PR2 context reads exercise real routes, capabilities and PostgreSQL policy."""

from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from app.db.session import get_session
from app.main import app
from app.presentation import assessment_routes
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.auth_helpers import (
    admin_principal,
    clear_principal_override,
    override_principal,
    principal,
    student_principal,
    teacher_principal,
)
from tests.integration.test_scan_grouping_database import grouping_database  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = "/api/assessment-core"


@pytest_asyncio.fixture
async def context_client(grouping_database, monkeypatch):  # noqa: F811
    connection, session, service, ids = grouping_database
    await service.initialize(ids["batch"], ids["teacher"])
    factory = async_sessionmaker(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    monkeypatch.setattr(assessment_routes, "async_session_factory", factory)

    async def db():
        yield session

    app.dependency_overrides[get_session] = db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, ids
    finally:
        app.dependency_overrides.pop(get_session, None)
        clear_principal_override(app)


def paths(ids, foreign=False):
    return [
        f"{ROOT}/assignments/{ids['other_assignment' if foreign else 'assignment']}",
        f"{ROOT}/assessments/{ids['other_assessment' if foreign else 'assessment']}",
        f"{ROOT}/assignments/{ids['other_assignment' if foreign else 'assignment']}/results?offset=0&limit=100",
        *[
            f"{ROOT}/scan-batches/{ids['foreign_batch' if foreign else 'batch']}{suffix}"
            for suffix in ("", "/artifacts", "/pages", "/matching", "/grouping")
        ],
    ]


@pytest.mark.parametrize("actor", ["teacher", "co_teacher", "admin"])
async def test_pr2_context_and_dtos_match_primary_teacher(context_client, actor):
    client, ids = context_client
    override_principal(app, teacher_principal(ids["teacher"]))
    expected = []
    for path in paths(ids):
        response = await client.get(path)
        assert response.status_code == 200, response.text
        expected.append(response.json())
    value = admin_principal(uuid4()) if actor == "admin" else teacher_principal(ids[actor])
    override_principal(app, value)
    for path, original in zip(paths(ids), expected, strict=True):
        response = await client.get(path)
        assert response.status_code == 200, response.text
        assert response.json() == original
    roster = expected[2]
    assert roster["participant_count"] == 2
    assert {item["assignment_participant_id"] for item in roster["items"]} == {
        str(ids["participant_a"]), str(ids["participant_b"])
    }
    sensitive = {
        "login", "email", "password", "password_hash", "user_id", "student_user_id",
        "storage_reference", "provider_matching_token", "group_token", "external_ref",
    }

    def check_keys(value):
        if isinstance(value, dict):
            assert not sensitive.intersection(value)
            for child in value.values():
                check_keys(child)
        elif isinstance(value, list):
            for child in value:
                check_keys(child)

    check_keys(roster)


@pytest.mark.parametrize("actor", ["outside", "unrelated", "student", "authenticated"])
async def test_context_denies_non_class_teachers(context_client, actor):
    client, ids = context_client
    values = {
        "outside": teacher_principal(ids["unrelated_teacher"]),
        "unrelated": teacher_principal(uuid4()),
        "student": student_principal(uuid4(), ids["student_a"]),
        "authenticated": principal(uuid4(), "unknown"),
    }
    override_principal(app, values[actor])
    for path in paths(ids):
        response = await client.get(path)
        assert response.status_code == (404 if actor in {"outside", "unrelated"} else 403)


async def test_co_teacher_cannot_substitute_foreign_objects(context_client):
    client, ids = context_client
    override_principal(app, teacher_principal(ids["co_teacher"]))
    for path in paths(ids, foreign=True):
        assert (await client.get(path)).status_code == 404
    checking = f"{ROOT}/scan-batches/{ids['foreign_batch']}/checking"
    assert (await client.get(checking)).status_code == 404
    assert (await client.post(checking + "/start")).status_code == 404
    assert (await client.post(checking + "/execute")).status_code == 404
    assert (await client.get(f"{ROOT}/scan-pages/{ids['foreign_page']}/content")).status_code == 404
    grouping = (await client.get(f"{ROOT}/scan-batches/{ids['batch']}/grouping")).json()
    response = await client.patch(
        f"{ROOT}/scan-batches/{ids['batch']}/grouping/pages/{ids['pages'][0]}",
        json={
            "assignment_participant_id": str(ids["other_participant"]),
            "expected_revision": grouping["grouping_revision"],
            "expected_row_version": grouping["row_version"],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "grouping_participant_not_in_assignment"


async def test_revoked_class_membership_denies_context(context_client, grouping_database):  # noqa: F811
    client, ids = context_client
    connection = grouping_database[0]
    await connection.execute(text(
        "DELETE FROM class_group_teachers WHERE class_group_id=:group AND teacher_user_id=:co_teacher"
    ), ids)
    override_principal(app, teacher_principal(ids["co_teacher"]))
    for path in paths(ids):
        assert (await client.get(path)).status_code == 404


async def test_membership_does_not_grant_unassigned_assessment(context_client, grouping_database):  # noqa: F811
    client, ids = context_client
    connection = grouping_database[0]
    assessment_id = uuid4()
    await connection.execute(text(
        "INSERT INTO assessments(id,title,status,created_by) VALUES (:id,'Private draft','draft',:owner)"
    ), {"id": assessment_id, "owner": ids["teacher"]})
    override_principal(app, teacher_principal(ids["co_teacher"]))
    assert (await client.get(f"{ROOT}/assessments/{assessment_id}")).status_code == 404


async def test_context_grant_does_not_grant_authoring_or_detailed_results(context_client):
    client, ids = context_client
    override_principal(app, teacher_principal(ids["co_teacher"]))
    response = await client.patch(
        f"{ROOT}/assessments/{ids['assessment']}",
        json={"title": "Unauthorized edit", "expected_updated_at": "2026-10-14T00:00:00Z"},
    )
    assert response.status_code == 404
    assert (await client.post(f"{ROOT}/assignments/{ids['assignment']}/close", json={})).status_code == 404
    assert (await client.get(
        f"{ROOT}/assignments/{ids['assignment']}/students/{ids['student_a']}/results"
    )).status_code == 404
