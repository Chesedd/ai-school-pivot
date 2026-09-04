"""Real-PostgreSQL acceptance for hierarchy-safe catalog option search."""
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

database_url = os.environ.get("TEST_DATABASE_URL", "")
if not database_url:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not database_url.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Integration cleanup is allowed only for a database ending in _test")
os.environ["DATABASE_URL"] = database_url

from app.application.catalog_options import CatalogOptionQuery, CatalogOptionService  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.main import app  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from tests.integration.auth_helpers import (clear_principal_override, override_principal,
    teacher_principal)  # noqa: E402

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean():
    async with async_session_factory() as db, db.begin():
        await db.execute(text("TRUNCATE curriculum_catalog_aliases, skills, subtopics, topics, grades, subjects, users CASCADE"))
    yield


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def dispose():
    yield
    await engine.dispose()


async def _id(db, sql, **values):
    return await db.scalar(text(sql + " RETURNING id"), values)


async def test_live_statuses_hierarchy_and_alias_replacement_lifecycle():
    async with async_session_factory() as db, db.begin():
        user = await _id(db, "INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES ('u','u','u','x')")
        grade7 = await _id(db, "INSERT INTO grades(number,name,normalized_name,status) VALUES (7,'7','7','active')")
        grade8 = await _id(db, "INSERT INTO grades(number,name,normalized_name,status) VALUES (8,'8','8','active')")
        math = await _id(db, "INSERT INTO subjects(code,name,normalized_name,status) VALUES ('math','Math','math','active')")
        provisional = await _id(db, "INSERT INTO subjects(code,name,normalized_name,status,proposed_by) VALUES ('prov','Provisional','provisional','provisional',:u)", u=user)
        dead_subject = await _id(db, "INSERT INTO subjects(code,name,normalized_name,status) VALUES ('dead','Dead','dead','deprecated')")
        physics = await _id(db, "INSERT INTO subjects(code,name,normalized_name,status) VALUES ('physics','Physics','physics','active')")
        a = await _id(db, "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status) VALUES (:s,:g,'a','A','a','deprecated')", s=math, g=grade7)
        b = await _id(db, "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status) VALUES (:s,:g,'b','B','b','deprecated')", s=math, g=grade7)
        c = await _id(db, "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status) VALUES (:s,:g,'c','Canonical','canonical','active')", s=math, g=grade7)
        await db.execute(text("UPDATE topics SET replacement_id=:b WHERE id=:a"), {"a": a, "b": b})
        await db.execute(text("UPDATE topics SET replacement_id=:c WHERE id=:b"), {"b": b, "c": c})
        wrong_grade = await _id(db, "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status) VALUES (:s,:g,'wg','Canonical','canonical','active')", s=math, g=grade8)
        cross_old = await _id(db, "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status) VALUES (:s,:g,'co','Cross old','cross old','deprecated')", s=math, g=grade7)
        cross_new = await _id(db, "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status) VALUES (:s,:g,'cn','Cross new','cross new','active')", s=physics, g=grade7)
        await db.execute(text("UPDATE topics SET replacement_id=:new WHERE id=:old"), {"new": cross_new, "old": cross_old})
        loop1 = await _id(db, "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status) VALUES (:s,:g,'l1','L1','l1','deprecated')", s=math, g=grade7)
        loop2 = await _id(db, "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status) VALUES (:s,:g,'l2','L2','l2','deprecated')", s=math, g=grade7)
        await db.execute(text("UPDATE topics SET replacement_id=:l2 WHERE id=:l1"), {"l1": loop1, "l2": loop2})
        await db.execute(text("UPDATE topics SET replacement_id=:l1 WHERE id=:l2"), {"l1": loop1, "l2": loop2})
        alias_sql = "INSERT INTO curriculum_catalog_aliases(kind,alias_name,normalized_alias,topic_target_id,subject_id,grade_id,created_by) VALUES ('topic',:name,:name,:target,:s,:g,:u)"
        for name, target in (("merged", a), ("cross", cross_old), ("loop", loop1)):
            await db.execute(text(alias_sql), {"name": name, "target": target, "s": math, "g": grade7, "u": user})
    async with async_session_factory() as db:
        subjects = await CatalogOptionService(db).search(CatalogOptionQuery("subjects", "", 20))
        assert {item["id"] for item in subjects["items"]} == {str(math), str(physics), str(provisional)}
        merged = await CatalogOptionService(db).search(CatalogOptionQuery("topics", "merged", 20, math, grade7))
        assert merged["items"] == [{"id": str(c), "name": "Canonical", "status": "active", "match": "alias"}]
        assert (await CatalogOptionService(db).search(CatalogOptionQuery("topics", "cross", 20, math, grade7)))["items"] == []
        assert (await CatalogOptionService(db).search(CatalogOptionQuery("topics", "loop", 20, math, grade7)))["items"] == []
        scoped = await CatalogOptionService(db).search(CatalogOptionQuery("topics", "canonical", 20, math, grade7))
        assert [item["id"] for item in scoped["items"]] == [str(c)] and str(wrong_grade) not in str(scoped)
        assert str(dead_subject) not in str(subjects)


async def test_route_bounds_parents_and_content_read_capability():
    user = uuid4()
    override_principal(app, teacher_principal(user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ("topics", "subtopics", "skills"):
            assert (await client.get(f"/api/content-bank/catalog/options/{path}" )).status_code == 422
        assert (await client.get("/api/content-bank/catalog/options/subjects", params={"limit": 0})).status_code == 422
        assert (await client.get("/api/content-bank/catalog/options/subjects", params={"limit": 21})).status_code == 422
        assert (await client.get("/api/content-bank/catalog/options/subjects", params={"q": "x" * 201})).status_code == 422
    clear_principal_override(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/content-bank/catalog/options/subjects")).status_code == 401
