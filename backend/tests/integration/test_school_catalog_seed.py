"""Real-PostgreSQL acceptance for the non-destructive starter seed."""
import json
import os

import pytest
import pytest_asyncio
from sqlalchemy import text

database_url = os.environ.get("TEST_DATABASE_URL", "")
if not database_url:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not database_url.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("Integration cleanup is allowed only for a database ending in _test")
os.environ["DATABASE_URL"] = database_url

from app.db.session import async_session_factory, engine  # noqa: E402
from app.tools.seed_school_catalog import DATA, seed_catalog  # noqa: E402

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean():
    async with async_session_factory() as db, db.begin():
        await db.execute(text("TRUNCATE skills, subtopics, topics, grades, subjects, users CASCADE"))
    yield


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def dispose():
    yield
    await engine.dispose()


async def test_full_seed_first_run_and_second_run_are_complete_and_idempotent():
    first = await seed_catalog(session_factory=async_session_factory)
    assert first["grades"]["created"] == 11
    assert all(first[kind]["created"] > 0 for kind in ("subjects", "topics", "subtopics", "skills"))
    async with async_session_factory() as db:
        before = {table: await db.scalar(text(f"SELECT count(*) FROM {table}"))
                  for table in ("grades", "subjects", "topics", "subtopics", "skills")}
        ids = (await db.execute(text("SELECT id FROM subjects ORDER BY id"))).scalars().all()
    second = await seed_catalog(session_factory=async_session_factory)
    assert all(second[kind]["created"] == 0 for kind in second)
    async with async_session_factory() as db:
        after = {table: await db.scalar(text(f"SELECT count(*) FROM {table}")) for table in before}
        assert (await db.execute(text("SELECT id FROM subjects ORDER BY id"))).scalars().all() == ids
        assert await db.scalar(text("SELECT count(*) FROM topics t JOIN subjects s ON s.id=t.subject_id JOIN grades g ON g.id=t.grade_id")) == after["topics"]
        assert await db.scalar(text("SELECT count(*) FROM skills s JOIN subtopics st ON st.id=s.subtopic_id")) == after["skills"]
        assert await db.scalar(text("SELECT count(*) FROM grades WHERE number BETWEEN 1 AND 11")) == 11
    assert before == after


async def test_deprecated_identity_resolves_or_conflicts_without_resurrection(tmp_path):
    data = {"subjects": [{"name": "Seed Subject", "grades": []},
                         {"name": "Rejected Subject", "grades": []}]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    async with async_session_factory() as db, db.begin():
        resolver = await db.scalar(text("INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES ('seed-resolver','seed-resolver','Seed Resolver','x') RETURNING id"))
        live = await db.scalar(text("INSERT INTO subjects(code,name,normalized_name,status) VALUES ('live','Renamed','renamed','active') RETURNING id"))
        old = await db.scalar(text("INSERT INTO subjects(code,name,normalized_name,status,replacement_id,resolved_by,resolved_at,resolution_reason) VALUES ('old','Seed Subject','seed subject','deprecated',:live,:resolver,clock_timestamp(),'integration fixture merge') RETURNING id"), {"live": live, "resolver": resolver})
        rejected = await db.scalar(text("INSERT INTO subjects(code,name,normalized_name,status,resolved_by,resolved_at,resolution_reason) VALUES ('rejected','Rejected Subject','rejected subject','deprecated',:resolver,clock_timestamp(),'integration fixture rejection') RETURNING id"), {"resolver": resolver})
    report = await seed_catalog(path, session_factory=async_session_factory)
    assert report["subjects"] == {"created": 0, "reused": 1, "conflicts": 1}
    async with async_session_factory() as db:
        assert await db.scalar(text("SELECT count(*) FROM subjects WHERE normalized_name IN ('seed subject','rejected subject')")) == 2
        assert await db.scalar(text("SELECT replacement_id FROM subjects WHERE id=:id"), {"id": old}) == live
        assert await db.scalar(text("SELECT replacement_id FROM subjects WHERE id=:id"), {"id": rejected}) is None


async def test_partial_active_and_provisional_identities_are_reused(tmp_path):
    data = {"subjects": [{"name": "Existing", "grades": []},
                         {"name": "Proposed", "grades": []}]}
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    async with async_session_factory() as db, db.begin():
        user = await db.scalar(text("INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES ('seed-user','seed-user','seed-user','x') RETURNING id"))
        active = await db.scalar(text("INSERT INTO subjects(code,name,normalized_name,status) VALUES ('existing','Existing','existing','active') RETURNING id"))
        provisional = await db.scalar(text("INSERT INTO subjects(code,name,normalized_name,status,proposed_by) VALUES ('proposed','Proposed','proposed','provisional',:u) RETURNING id"), {"u": user})
    report = await seed_catalog(path, session_factory=async_session_factory)
    assert report["subjects"] == {"created": 0, "reused": 2, "conflicts": 0}
    async with async_session_factory() as db:
        assert await db.scalar(text("SELECT id FROM subjects WHERE normalized_name='existing'")) == active
        assert await db.scalar(text("SELECT id FROM subjects WHERE normalized_name='proposed'")) == provisional
