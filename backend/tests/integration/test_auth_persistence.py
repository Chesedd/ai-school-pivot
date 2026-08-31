"""PostgreSQL integrity proofs for account persistence."""

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

URL = os.environ.get("TEST_DATABASE_URL", "")
if not URL:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("account persistence tests require a database ending in _test")
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def engine():
    value = create_async_engine(URL)
    try:
        yield value
    finally:
        await value.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean(engine):
    async with engine.begin() as c:
        await c.execute(
            text(
                "TRUNCATE auth_sessions, user_roles, student_user_links, users CASCADE"
            )
        )
    yield


async def rejected(c, sql, values=None):
    with pytest.raises(IntegrityError):
        async with c.begin_nested():
            await c.execute(text(sql), values or {})


async def make_user(
    c,
    login="Teacher",
    normalized="teacher",
    display="Teacher",
    password_hash="$argon2id$opaque",
):
    return (
        (
            await c.execute(
                text(
                    "INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES (:l,:n,:d,:p) RETURNING *"
                ),
                {"l": login, "n": normalized, "d": display, "p": password_hash},
            )
        )
        .mappings()
        .one()
    )


async def test_user_defaults_validation_uniqueness_and_opaque_hash(engine):
    async with engine.begin() as c:
        row = await make_user(c)
        assert isinstance(row["id"], UUID) and row["is_active"] is True
        assert row["created_at"].tzinfo and row["updated_at"].tzinfo
        assert row["password_hash"] == "$argon2id$opaque"
        await rejected(
            c,
            "INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES ('teacher','teacher','Other','hash')",
        )
        for login, normalized, display in (
            ("", "x", "Name"),
            (" x ", "x", "Name"),
            ("x", "x", ""),
            ("x", "x", " Name "),
        ):
            await rejected(
                c,
                "INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES (:l,:n,:d,'hash')",
                {"l": login, "n": normalized, "d": display},
            )
        await rejected(
            c,
            "INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES ('x','x','Name',NULL)",
        )


async def test_roles_are_bounded_unique_and_referential(engine):
    async with engine.begin() as c:
        user = await make_user(c)
        await c.execute(
            text("INSERT INTO user_roles VALUES (:id,'teacher'),(:id,'admin')"),
            {"id": user["id"]},
        )
        assert set(
            (
                await c.execute(
                    text("SELECT role FROM user_roles WHERE user_id=:id"),
                    {"id": user["id"]},
                )
            ).scalars()
        ) == {"teacher", "admin"}
        await rejected(
            c, "INSERT INTO user_roles VALUES (:id,'teacher')", {"id": user["id"]}
        )
        await rejected(
            c, "INSERT INTO user_roles VALUES (:id,'superuser')", {"id": user["id"]}
        )
        await rejected(
            c, "INSERT INTO user_roles VALUES (:id,'student')", {"id": uuid4()}
        )


async def test_sessions_validate_hash_time_uniqueness_and_user(engine):
    now = datetime.now(timezone.utc)
    async with engine.begin() as c:
        user = await make_user(c)
        session = (
            (
                await c.execute(
                    text(
                        "INSERT INTO auth_sessions(user_id,token_hash,expires_at) VALUES (:u,:h,:e) RETURNING *"
                    ),
                    {"u": user["id"], "h": b"a" * 32, "e": now + timedelta(hours=1)},
                )
            )
            .mappings()
            .one()
        )
        assert session["user_id"] == user["id"]
        await rejected(
            c,
            "INSERT INTO auth_sessions(user_id,token_hash,expires_at) VALUES (:u,:h,:e)",
            {"u": user["id"], "h": b"a" * 32, "e": now + timedelta(hours=2)},
        )
        await rejected(
            c,
            "INSERT INTO auth_sessions(user_id,token_hash,expires_at) VALUES (:u,:h,:e)",
            {"u": user["id"], "h": b"short", "e": now + timedelta(hours=1)},
        )
        await rejected(
            c,
            "INSERT INTO auth_sessions(user_id,token_hash,expires_at) VALUES (:u,:h,clock_timestamp()-interval '1 second')",
            {"u": user["id"], "h": b"b" * 32},
        )
        await rejected(
            c,
            "UPDATE auth_sessions SET revoked_at=created_at-interval '1 second' WHERE id=:id",
            {"id": session["id"]},
        )
        await rejected(
            c,
            "INSERT INTO auth_sessions(user_id,token_hash,expires_at) VALUES (:u,:h,:e)",
            {"u": uuid4(), "h": b"c" * 32, "e": now + timedelta(hours=1)},
        )


async def test_student_links_are_one_to_one_and_referential(engine):
    async with engine.begin() as c:
        u1 = await make_user(c)
        u2 = await make_user(c, "Other", "other", "Other")
        actor = uuid4()
        group = uuid4()
        s1 = uuid4()
        s2 = uuid4()
        await c.execute(
            text(
                "INSERT INTO class_groups(id,name,created_by) VALUES (:g,'Auth test',:a)"
            ),
            {"g": group, "a": actor},
        )
        await c.execute(
            text(
                "INSERT INTO students(id,class_group_id,display_name) VALUES (:s1,:g,'One'),(:s2,:g,'Two')"
            ),
            {"s1": s1, "s2": s2, "g": group},
        )
        await c.execute(
            text("INSERT INTO student_user_links(user_id,student_id) VALUES (:u,:s)"),
            {"u": u1["id"], "s": s1},
        )
        await rejected(
            c,
            "INSERT INTO student_user_links(user_id,student_id) VALUES (:u,:s)",
            {"u": u1["id"], "s": s2},
        )
        await rejected(
            c,
            "INSERT INTO student_user_links(user_id,student_id) VALUES (:u,:s)",
            {"u": u2["id"], "s": s1},
        )
        await rejected(
            c,
            "INSERT INTO student_user_links(user_id,student_id) VALUES (:u,:s)",
            {"u": uuid4(), "s": s2},
        )
        await rejected(
            c,
            "INSERT INTO student_user_links(user_id,student_id) VALUES (:u,:s)",
            {"u": u2["id"], "s": uuid4()},
        )
        await rejected(c, "DELETE FROM users WHERE id=:u", {"u": u1["id"]})
        await rejected(c, "DELETE FROM students WHERE id=:s", {"s": s1})
