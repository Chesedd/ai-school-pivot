"""Class discovery uses real PostgreSQL membership without widening authoring."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.application.object_access import ObjectAccessScope
from app.infrastructure.assessment_repository import SQLAlchemyAssessmentRepository
from app.main import app
from tests.integration.auth_helpers import (
    admin_principal,
    override_principal,
    principal,
    student_principal,
    teacher_principal,
)
from tests.integration.test_paper_teacher_context_routes import context_client  # noqa: F401
from tests.integration.test_scan_grouping_database import grouping_database  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = "/api/assessment-core"


def list_path(ids, group="group"):
    return f"{ROOT}/class-groups/{ids[group]}/assignments"


@pytest.mark.parametrize("actor", ["teacher", "co_teacher", "admin"])
async def test_normal_class_flow_and_unchanged_dto(context_client, actor):  # noqa: F811
    client, ids = context_client
    override_principal(app, teacher_principal(ids["teacher"]))
    expected = (await client.get(list_path(ids))).json()
    assert expected["total"] == 1
    assert set(expected) == {"items", "total", "offset", "limit"}
    assert set(expected["items"][0]) == {
        "id", "assessment_id", "assessment_title", "class_group_id", "status",
        "start_at", "due_at", "max_attempts", "participant_count", "created_at", "closed_at",
    }
    value = admin_principal(uuid4()) if actor == "admin" else teacher_principal(ids[actor])
    override_principal(app, value)
    classes = await client.get("/api/classrooms")
    assert classes.status_code == 200
    assert str(ids["group"]) in {item["id"] for item in classes.json()["items"]}
    assert (await client.get(f"/api/classrooms/{ids['group']}")).status_code == 200
    response = await client.get(list_path(ids))
    assert response.status_code == 200
    assert response.json() == expected
    assignment_id = response.json()["items"][0]["id"]
    assert assignment_id == str(ids["assignment"])
    for path in (
        f"{ROOT}/assignments/{assignment_id}",
        f"{ROOT}/assessments/{ids['assessment']}",
        f"{ROOT}/assignments/{assignment_id}/results",
    ):
        assert (await client.get(path)).status_code == 200


@pytest.mark.parametrize("actor", ["outside", "other_class", "student", "authenticated"])
async def test_teacher_list_denies_non_members(context_client, actor):  # noqa: F811
    client, ids = context_client
    values = {
        "outside": teacher_principal(uuid4()),
        "other_class": teacher_principal(ids["unrelated_teacher"]),
        "student": student_principal(uuid4(), ids["student_a"]),
        "authenticated": principal(uuid4(), "unknown"),
    }
    override_principal(app, values[actor])
    response = await client.get(list_path(ids))
    assert response.status_code == (404 if actor in {"outside", "other_class"} else 403)
    assert "items" not in response.json()


async def test_revocation_removes_discovery(context_client, grouping_database):  # noqa: F811
    client, ids = context_client
    override_principal(app, teacher_principal(ids["co_teacher"]))
    assert (await client.get(list_path(ids))).json()["total"] == 1
    await grouping_database[0].execute(text(
        "DELETE FROM class_group_teachers WHERE class_group_id=:group AND teacher_user_id=:co_teacher"
    ), ids)
    assert (await client.get(list_path(ids))).status_code == 404
    assert (await client.get(f"{ROOT}/assignments/{ids['assignment']}")).status_code == 404
    page = await SQLAlchemyAssessmentRepository(grouping_database[1]).list_assignments_for_class(
        ids["group"], "all", 0, 50, ObjectAccessScope(ids["co_teacher"], False)
    )
    assert page["total"] == 0 and page["items"] == []


async def test_foreign_class_and_assignment_substitution(context_client, grouping_database):  # noqa: F811
    client, ids = context_client
    override_principal(app, teacher_principal(ids["co_teacher"]))
    response = await client.get(list_path(ids), params={
        "class_group_id": str(ids["other_group"]), "assignment_id": str(ids["other_assignment"]),
    })
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(ids["assignment"])]
    assert (await client.get(list_path(ids, "other_group"))).status_code == 404
    assert (await client.get(f"{ROOT}/assignments/{ids['other_assignment']}")).status_code == 404
    page = await SQLAlchemyAssessmentRepository(grouping_database[1]).list_assignments_for_class(
        ids["other_group"], "all", 0, 50, ObjectAccessScope(ids["co_teacher"], False)
    )
    assert page["total"] == 0 and page["items"] == []


async def test_status_pagination_and_author_list_stay_scoped(context_client):  # noqa: F811
    client, ids = context_client
    override_principal(app, teacher_principal(ids["co_teacher"]))
    assert (await client.get(list_path(ids), params={"status": "open"})).json()["total"] == 1
    assert (await client.get(list_path(ids), params={"status": "closed"})).json()["total"] == 0
    page = (await client.get(list_path(ids), params={"offset": 1, "limit": 1})).json()
    assert page == {"items": [], "total": 1, "offset": 1, "limit": 1}
    # General authoring navigation retains creator-only semantics.
    assert (await client.get(f"{ROOT}/assessments")).json()["total"] == 0
    assert (await client.get(f"{ROOT}/assessments/{ids['assessment']}/assignments")).status_code == 404
    override_principal(app, teacher_principal(ids["teacher"]))
    assert (await client.post(f"{ROOT}/assignments/{ids['assignment']}/close", json={})).status_code == 200
    override_principal(app, teacher_principal(ids["co_teacher"]))
    assert (await client.get(list_path(ids), params={"status": "closed"})).json()["total"] == 1
    assert (await client.get(list_path(ids), params={"status": "open"})).json()["total"] == 0


@pytest.mark.parametrize("actor", ["teacher", "co_teacher", "admin"])
async def test_discovery_retains_real_authoring_boundaries(context_client, grouping_database, actor):  # noqa: F811
    client, ids = context_client
    connection = grouping_database[0]
    draft, variant, assignment = uuid4(), uuid4(), uuid4()
    values = {**ids, "draft": draft, "draft_variant": variant, "draft_assignment": assignment}
    for statement in (
        "INSERT INTO assessments(id,title,status,created_by) VALUES (:draft,'Authoring draft','draft',:teacher)",
        "INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES (:draft_variant,:draft,'A',1)",
        """INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by)
           VALUES (:draft_assignment,:draft,:group,clock_timestamp(),clock_timestamp()+interval '1 day',:teacher)""",
    ):
        await connection.execute(text(statement), values)
    override_principal(app, admin_principal(uuid4()) if actor == "admin" else teacher_principal(ids[actor]))
    listing = await client.get(list_path(ids))
    assert listing.status_code == 200
    assert str(assignment) in {item["id"] for item in listing.json()["items"]}
    opened = await client.get(f"{ROOT}/assessments/{draft}")
    assert opened.status_code == 200
    response = await client.patch(f"{ROOT}/assessments/{draft}", json={
        "title": "Edited by authorized author", "expected_updated_at": opened.json()["updated_at"],
    })
    assert response.status_code == (404 if actor == "co_teacher" else 200)
    response = await client.post(f"{ROOT}/assessments/{draft}/variants", json={"name": "B"})
    assert response.status_code == (404 if actor == "co_teacher" else 201)
    response = await client.delete(f"{ROOT}/assessments/{draft}/variants/{variant}")
    assert response.status_code == (404 if actor == "co_teacher" else 204)
    response = await client.post(f"{ROOT}/assignments/{ids['assignment']}/close", json={})
    assert response.status_code == (404 if actor == "co_teacher" else 200)
    if actor == "co_teacher":
        assert (await client.get(f"{ROOT}/assessments/{draft}")).json()["title"] == "Authoring draft"
