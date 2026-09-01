"""Real PostgreSQL acceptance for provisional curriculum persistence."""

import asyncio
import os
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

URL = os.environ.get("TEST_DATABASE_URL", "")
if not URL:
    pytest.skip("TEST_DATABASE_URL is required", allow_module_level=True)
if not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("catalog tests require *_test")
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def engine():
    e = create_async_engine(URL)
    async with e.begin() as c:
        await c.execute(
            text(
                "TRUNCATE tasks, skills, subtopics, topics, grades, subjects, users CASCADE"
            )
        )
    yield e
    await e.dispose()


async def user(c):
    return await c.scalar(
        text(
            "INSERT INTO users(login,normalized_login,display_name,password_hash) VALUES (:x,:x,:x,'hash') RETURNING id"
        ),
        {"x": str(uuid4())},
    )


async def parents(c, a):
    s = await c.scalar(
        text(
            "INSERT INTO subjects(code,name,normalized_name,status,proposed_by) VALUES ('math','Математика','математика','provisional',:a) RETURNING id"
        ),
        {"a": a},
    )
    g = await c.scalar(
        text(
            "INSERT INTO grades(number,name,normalized_name,status,proposed_by) VALUES (8,'8 класс','8 класс','provisional',:a) RETURNING id"
        ),
        {"a": a},
    )
    return s, g


async def test_every_kind_lifecycle_attribution_hierarchy_and_history(engine):
    async with engine.begin() as c:
        a = await user(c)
        s, g = await parents(c, a)
        t = await c.scalar(
            text(
                "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status,proposed_by) VALUES (:s,:g,'quad','Квадратные','квадратные','provisional',:a) RETURNING id"
            ),
            locals(),
        )
        sub = await c.scalar(
            text(
                "INSERT INTO subtopics(topic_id,code,name,normalized_name,status,proposed_by) VALUES (:t,'vieta','Виета','виета','provisional',:a) RETURNING id"
            ),
            locals(),
        )
        sk = await c.scalar(
            text(
                "INSERT INTO skills(subtopic_id,code,name,normalized_name,status,proposed_by) VALUES (:sub,'apply','Применять','применять','provisional',:a) RETURNING id"
            ),
            locals(),
        )
        for table, i in zip(
            ("subjects", "grades", "topics", "subtopics", "skills"), (s, g, t, sub, sk)
        ):
            assert (
                await c.execute(
                    text(f"SELECT status,proposed_by FROM {table} WHERE id=:i"),
                    {"i": i},
                )
            ).one() == ("provisional", a)
            with pytest.raises(IntegrityError):
                async with c.begin_nested():
                    await c.execute(
                        text(f"UPDATE {table} SET proposed_by=NULL WHERE id=:i"),
                        {"i": i},
                    )
        await c.execute(
            text("UPDATE skills SET status='deprecated' WHERE id=:i"), {"i": sk}
        )
        assert (
            await c.scalar(text("SELECT id FROM skills WHERE id=:i"), {"i": sk}) == sk
        )


async def test_content_bank_draft_accepts_provisional_canonical_foreign_keys(engine):
    """A draft uses the ordinary catalog FKs; proposal workflow is out of scope."""
    async with engine.begin() as c:
        a = await user(c)
        s, g = await parents(c, a)
        t = await c.scalar(
            text(
                "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status,proposed_by) "
                "VALUES (:s,:g,'draft-topic','Черновик','черновик','provisional',:a) RETURNING id"
            ),
            locals(),
        )
        sub = await c.scalar(
            text(
                "INSERT INTO subtopics(topic_id,code,name,normalized_name,status,proposed_by) "
                "VALUES (:t,'draft-subtopic','Раздел','раздел','provisional',:a) RETURNING id"
            ),
            locals(),
        )
        sk = await c.scalar(
            text(
                "INSERT INTO skills(subtopic_id,code,name,normalized_name,status,proposed_by) "
                "VALUES (:sub,'draft-skill','Навык','навык','provisional',:a) RETURNING id"
            ),
            locals(),
        )
        task = await c.scalar(
            text(
                "INSERT INTO tasks(subject_id,grade_id,topic_id,subtopic_id,created_by) "
                "VALUES (:s,:g,:t,:sub,:a) RETURNING id"
            ),
            locals(),
        )
        version = await c.scalar(
            text(
                "INSERT INTO task_versions(task_id,version_no,statement,task_type,answer_format,difficulty,created_by) "
                "VALUES (:task,1,'Черновик задания','calculation','number',25,:a) RETURNING id"
            ),
            locals(),
        )
        await c.execute(
            text(
                "INSERT INTO task_skill_links(task_version_id,skill_id,weight,is_primary) "
                "VALUES (:version,:sk,1,true)"
            ),
            locals(),
        )

        assert (
            await c.execute(
                text(
                    "SELECT t.subject_id,t.grade_id,t.topic_id,t.subtopic_id,v.status,l.skill_id "
                    "FROM tasks t JOIN task_versions v ON v.task_id=t.id "
                    "JOIN task_skill_links l ON l.task_version_id=v.id WHERE t.id=:task"
                ),
                locals(),
            )
        ).one() == (s, g, t, sub, "draft", sk)

        columns = set(
            (await c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name='tasks'"
                )
            )).scalars()
        )
        assert {"subject_id", "grade_id", "topic_id", "subtopic_id"} <= columns
        assert not any("proposal" in name or "metadata" in name for name in columns)


async def test_live_duplicate_conflicts_and_deprecated_identity_is_reusable(engine):
    async with engine.begin() as c:
        a = await user(c)
        sid = await c.scalar(
            text(
                "INSERT INTO subjects(code,name,normalized_name) VALUES ('a','Math','math') RETURNING id"
            )
        )
        assert (
            await c.scalar(text("SELECT status FROM subjects WHERE id=:i"), {"i": sid})
            == "active"
        )
        with pytest.raises(IntegrityError):
            async with c.begin_nested():
                await c.execute(
                    text(
                        "INSERT INTO subjects(code,name,normalized_name,status,proposed_by) VALUES ('b','MATH','math','provisional',:a)"
                    ),
                    {"a": a},
                )
        await c.execute(
            text("UPDATE subjects SET status='deprecated' WHERE id=:i"), {"i": sid}
        )
        await c.execute(
            text(
                "INSERT INTO subjects(code,name,normalized_name,status,proposed_by) VALUES ('b','MATH','math','provisional',:a)"
            ),
            {"a": a},
        )


async def test_concurrent_topic_insert_has_exactly_one_winner(engine):
    async with engine.begin() as c:
        a = await user(c)
        s, g = await parents(c, a)

    async def insert(code):
        try:
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO topics(subject_id,grade_id,code,name,normalized_name,status,proposed_by) VALUES (:s,:g,:code,'Дроби','дроби','provisional',:a)"
                    ),
                    locals(),
                )
            return "created"
        except IntegrityError:
            return "conflict"

    assert sorted(await asyncio.gather(insert("f1"), insert("f2"))) == [
        "conflict",
        "created",
    ]
    async with engine.connect() as c:
        assert (
            await c.scalar(
                text(
                    "SELECT count(*) FROM topics WHERE subject_id=:s AND grade_id=:g AND normalized_name='дроби'"
                ),
                locals(),
            )
            == 1
        )
