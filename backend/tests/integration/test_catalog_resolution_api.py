"""J1F acceptance through real PostgreSQL, Alembic-head schema, and HTTP transactions."""

import asyncio
import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
if not DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not DATABASE_URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("J1F cleanup is allowed only for a database ending in _test")
os.environ["DATABASE_URL"] = DATABASE_URL

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.application.principal import Principal  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.main import app  # noqa: E402
from tests.integration.auth_helpers import (  # noqa: E402
    admin_principal, clear_principal_override, override_principal,
    student_principal, teacher_principal,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def clean():
    async with async_session_factory() as session, session.begin():
        assert (await session.scalar(text("select current_database()"))).endswith("_test")
        await session.execute(text("TRUNCATE skills, subtopics, topics, grades, subjects, users CASCADE"))


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def isolated_database():
    await clean(); clear_principal_override(app)
    yield
    clear_principal_override(app); await clean()


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def close_shared_engine():
    yield
    await engine.dispose()


async def user(name: str) -> UUID:
    async with async_session_factory() as session, session.begin():
        return await session.scalar(text("INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES (:n,:n,:n,'hash') RETURNING id"), {"n": f"{name}-{uuid4()}"})


async def seed(table: str, columns: str, values: str, **params) -> UUID:
    async with async_session_factory() as session, session.begin():
        return await session.scalar(text(f"INSERT INTO {table} ({columns}) VALUES ({values}) RETURNING id"), params)


async def request(method: str, path: str, payload=None, origin=None):
    headers = {"Origin": origin} if origin else None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=payload, headers=headers)


async def provisional_subject(name: str, proposer: UUID) -> UUID:
    return await seed("subjects", "code,name,normalized_name,status,proposed_by", ":c,:n,:nn,'provisional',:p", c=f"p-{uuid4()}", n=name, nn=name.casefold(), p=proposer)


async def active_subject(name: str) -> UUID:
    return await seed("subjects", "code,name,normalized_name,status", ":c,:n,:nn,'active'", c=f"a-{uuid4()}", n=name, nn=name.casefold())


async def test_real_http_authorization_and_trusted_origin_matrix():
    admin, teacher = await user("admin"), await user("teacher")
    source, target, rejected = await provisional_subject("Confirm", teacher), await active_subject("Canonical"), await provisional_subject("Reject", teacher)
    override_principal(app, admin_principal(admin))
    assert (await request("GET", "/api/catalog/proposals")).status_code == 200
    evil = await request("POST", f"/api/catalog/proposals/subject/{source}/confirm", {}, "https://evil.example")
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "foreign_origin"
    for principal in (teacher_principal(teacher), student_principal(uuid4(), uuid4()), Principal(uuid4(), "none", "None", frozenset(), frozenset(), None)):
        override_principal(app, principal)
        assert (await request("GET", "/api/catalog/proposals")).status_code == 403
        for action, body in (("confirm", {}), ("merge", {"target_id": str(target), "reason": "x"}), ("reject", {"reason": "x"})):
            assert (await request("POST", f"/api/catalog/proposals/subject/{source}/{action}", body)).status_code == 403
    clear_principal_override(app)
    assert (await request("GET", "/api/catalog/proposals")).status_code == 401
    override_principal(app, admin_principal(admin))
    assert (await request("POST", f"/api/catalog/proposals/subject/{source}/confirm", {})).status_code == 200
    assert (await request("POST", f"/api/catalog/proposals/subject/{rejected}/reject", {"reason": "unused"})).status_code == 200
    merge_source = await provisional_subject("Merge", teacher)
    assert (await request("POST", f"/api/catalog/proposals/subject/{merge_source}/merge", {"target_id": str(target), "reason": "duplicate"})).status_code == 200


async def test_proposal_list_filter_paging_and_lifecycle_exclusion():
    admin, teacher = await user("admin"), await user("teacher")
    subject = await provisional_subject("Visible subject", teacher)
    grade = await seed("grades", "number,name,normalized_name,status,proposed_by", "7,'Seven','seven','provisional',:p", p=teacher)
    await active_subject("Active")
    deprecated = await provisional_subject("Deprecated", teacher)
    async with async_session_factory() as session, session.begin():
        await session.execute(text("UPDATE subjects SET status='deprecated',resolved_by=:a,resolved_at=now(),resolution_reason='no' WHERE id=:id"), {"a": admin, "id": deprecated})
    override_principal(app, admin_principal(admin))
    all_rows = (await request("GET", "/api/catalog/proposals?offset=0&limit=100")).json()
    assert {x["id"] for x in all_rows} == {str(subject), str(grade)}
    assert all(x["status"] == "provisional" and x["proposed_by"] == str(teacher) for x in all_rows)
    filtered = (await request("GET", "/api/catalog/proposals?kind=grade&limit=1")).json()
    assert len(filtered) == 1 and filtered[0]["id"] == str(grade) and filtered[0]["number"] == 7
    assert (await request("GET", "/api/catalog/proposals?offset=1&limit=1")).json()[0]["id"] in {str(subject), str(grade)}


async def test_confirm_preserves_identity_and_attribution_and_becomes_visible():
    admin, teacher = await user("admin"), await user("teacher")
    subject = await provisional_subject("Geometry", teacher)
    override_principal(app, admin_principal(admin))
    response = await request("POST", f"/api/catalog/proposals/subject/{subject}/confirm", {})
    assert response.status_code == 200 and response.json()["id"] == str(subject) and response.json()["status"] == "active"
    async with async_session_factory() as session:
        row = (await session.execute(text("SELECT status,proposed_by,resolved_by,resolved_at,replacement_id FROM subjects WHERE id=:id"), {"id": subject})).one()
    assert row.status == "active" and row.proposed_by == teacher and row.resolved_by == admin and row.resolved_at is not None and row.replacement_id is None
    visible = await request("GET", "/api/content-bank/catalog/subjects")
    assert str(subject) in {x["id"] for x in visible.json()["items"]}


async def test_parent_first_full_hierarchy_and_effective_parent_canonicalization():
    admin, teacher = await user("admin"), await user("teacher")
    subject = await provisional_subject("Subject A", teacher)
    grade = await seed("grades", "number,name,normalized_name,status,proposed_by", "8,'Eight','eight','provisional',:p", p=teacher)
    topic = await seed("topics", "subject_id,grade_id,code,name,normalized_name,status,proposed_by", ":s,:g,'t','Topic','topic','provisional',:p", s=subject, g=grade, p=teacher)
    subtopic = await seed("subtopics", "topic_id,code,name,normalized_name,status,proposed_by", ":t,'st','Subtopic','subtopic','provisional',:p", t=topic, p=teacher)
    skill = await seed("skills", "subtopic_id,code,name,normalized_name,status,proposed_by", ":st,'sk','Skill','skill','provisional',:p", st=subtopic, p=teacher)
    override_principal(app, admin_principal(admin))
    for kind, value in (("topic", topic), ("subtopic", subtopic), ("skill", skill)):
        blocked = await request("POST", f"/api/catalog/proposals/{kind}/{value}/confirm", {})
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "catalog_parent_unresolved"
    for kind, value in (("subject", subject), ("grade", grade), ("topic", topic), ("subtopic", subtopic), ("skill", skill)):
        assert (await request("POST", f"/api/catalog/proposals/{kind}/{value}/confirm", {})).status_code == 200
    async with async_session_factory() as session:
        for table, value in (("subjects", subject), ("grades", grade), ("topics", topic), ("subtopics", subtopic), ("skills", skill)):
            row = (await session.execute(text(f"SELECT status,proposed_by,resolved_by,resolved_at FROM {table} WHERE id=:id"), {"id": value})).one()
            assert row.status == "active" and row.proposed_by == teacher and row.resolved_by == admin and row.resolved_at is not None


async def test_parent_merge_then_child_confirm_uses_active_replacement():
    admin, teacher = await user("admin"), await user("teacher")
    source, target = await provisional_subject("Alias", teacher), await active_subject("Canonical")
    grade = await seed("grades", "number,name,normalized_name,status", "9,'Nine','nine','active'")
    topic = await seed("topics", "subject_id,grade_id,code,name,normalized_name,status,proposed_by", ":s,:g,'t','Child','child','provisional',:p", s=source, g=grade, p=teacher)
    override_principal(app, admin_principal(admin))
    assert (await request("POST", f"/api/catalog/proposals/subject/{source}/merge", {"target_id": str(target), "reason": "same"})).status_code == 200
    assert (await request("POST", f"/api/catalog/proposals/topic/{topic}/confirm", {})).status_code == 200
    async with async_session_factory() as session:
        source_row = (await session.execute(text("SELECT status,replacement_id FROM subjects WHERE id=:id"), {"id": source})).one()
        topic_row = (await session.execute(text("SELECT status,subject_id FROM topics WHERE id=:id"), {"id": topic})).one()
    assert source_row == ("deprecated", target) and topic_row == ("active", target)


async def test_merge_alias_reuse_rejected_reproposal_and_target_validation():
    admin, teacher = await user("admin"), await user("teacher")
    source, target = await provisional_subject("Old label", teacher), await active_subject("Canonical")
    override_principal(app, admin_principal(admin))
    for target_id, expected in ((source, "catalog_merge_target_invalid"), (uuid4(), "catalog_merge_target_not_found")):
        response = await request("POST", f"/api/catalog/proposals/subject/{source}/merge", {"target_id": str(target_id), "reason": "duplicate"})
        assert response.json()["error"]["code"] == expected
    merged = await request("POST", f"/api/catalog/proposals/subject/{source}/merge", {"target_id": str(target), "reason": "duplicate"})
    assert merged.status_code == 200
    override_principal(app, teacher_principal(teacher))
    alias = await request("POST", "/api/catalog/proposals", {"kind": "subject", "name": " OLD LABEL "})
    assert alias.status_code == 200 and alias.json()["outcome"] == "existing_active" and alias.json()["id"] == str(target)
    rejected = await provisional_subject("Try again", teacher)
    override_principal(app, admin_principal(admin))
    assert (await request("POST", f"/api/catalog/proposals/subject/{rejected}/reject", {"reason": "bad"})).status_code == 200
    override_principal(app, teacher_principal(teacher))
    reproposed = await request("POST", "/api/catalog/proposals", {"kind": "subject", "name": "Try again"})
    assert reproposed.status_code == 201 and reproposed.json()["outcome"] == "created_provisional" and reproposed.json()["id"] != str(rejected)
    async with async_session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM subjects WHERE normalized_name='old label'")) == 1
        assert await session.scalar(text("SELECT count(*) FROM subjects WHERE normalized_name='try again'")) == 2


async def test_reject_in_use_descendant_and_concurrent_terminal_actions_are_atomic():
    admin, teacher = await user("admin"), await user("teacher")
    parent = await provisional_subject("Parent", teacher)
    grade = await seed("grades", "number,name,normalized_name,status", "10,'Ten','ten','active'")
    await seed("topics", "subject_id,grade_id,code,name,normalized_name,status,proposed_by", ":s,:g,'child','Child','child','provisional',:p", s=parent, g=grade, p=teacher)
    override_principal(app, admin_principal(admin))
    blocked = await request("POST", f"/api/catalog/proposals/subject/{parent}/reject", {"reason": "unused"})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "catalog_proposal_in_use"
    raced = await provisional_subject("Race", teacher)
    confirm, reject = await asyncio.gather(
        request("POST", f"/api/catalog/proposals/subject/{raced}/confirm", {}),
        request("POST", f"/api/catalog/proposals/subject/{raced}/reject", {"reason": "race"}),
    )
    outcomes = sorted((confirm.status_code, reject.status_code))
    assert outcomes == [200, 409]
    loser = confirm if confirm.status_code == 409 else reject
    assert loser.json()["error"]["code"] == "catalog_proposal_already_resolved"
    async with async_session_factory() as session:
        row = (await session.execute(text("SELECT status,resolved_by,resolved_at,replacement_id FROM subjects WHERE id=:id"), {"id": raced})).one()
    assert row.status in {"active", "deprecated"} and row.resolved_by == admin and row.resolved_at is not None and row.replacement_id is None
