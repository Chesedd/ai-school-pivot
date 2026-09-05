"""Real-PostgreSQL acceptance coverage for the Physics starter bridge."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


URL = os.environ.get("TEST_DATABASE_URL", "")
if URL and not URL.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError("migration tests require a database ending in _test")

pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")]
BACKEND = Path(__file__).parents[2]
PARENT = "20260904_01"
REVISION = "20260905_01"


def alembic(*arguments: str, succeeds: bool = True) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = URL
    completed = subprocess.run(["alembic", *arguments], cwd=BACKEND,
                               env=environment, capture_output=True, text=True)
    if succeeds:
        assert completed.returncode == 0, completed.stdout + completed.stderr
    else:
        assert completed.returncode != 0
        assert "Manual catalog reconciliation is required" in completed.stderr
    return completed


@pytest_asyncio.fixture(autouse=True)
async def parent_schema():
    engine = create_async_engine(URL)
    async with engine.begin() as connection:
        await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
        await connection.execute(sa.text("CREATE SCHEMA public"))
    await engine.dispose()
    alembic("upgrade", PARENT)
    yield
    engine = create_async_engine(URL)
    async with engine.begin() as connection:
        await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
        await connection.execute(sa.text("CREATE SCHEMA public"))
    await engine.dispose()
    alembic("upgrade", "head")


async def insert_starter(connection, *, target=False, extra_subtopic=False,
                         task_subtopic="none"):
    ids = {name: uuid4() for name in ("subject", "grade", "topic", "subtopic",
                                      "speed", "graph")}
    await connection.execute(sa.text("""
        INSERT INTO subjects (id, code, name, normalized_name, status)
        VALUES (:id, 'physics', 'Физика', 'физика', 'active')
    """), {"id": ids["subject"]})
    await connection.execute(sa.text("""
        INSERT INTO grades (id, number, name, normalized_name, status)
        VALUES (:id, 7, '7 класс', '7 класс', 'active')
    """), {"id": ids["grade"]})
    await connection.execute(sa.text("""
        INSERT INTO topics (id, subject_id, grade_id, code, name, normalized_name, status)
        VALUES (:id, :subject, :grade, 'historical-mechanics', 'Механика', 'механика', 'active')
    """), {"id": ids["topic"], "subject": ids["subject"], "grade": ids["grade"]})
    await connection.execute(sa.text("""
        INSERT INTO subtopics (id, topic_id, code, name, normalized_name, status)
        VALUES (:id, :topic, 'uniform-motion', 'Равномерное движение',
                'равномерное движение', 'active')
    """), {"id": ids["subtopic"], "topic": ids["topic"]})
    for key, code, name, normalized in (
        ("speed", "speed", "Вычислять скорость", "вычислять скорость"),
        ("graph", "graph", "Строить график движения", "строить график движения"),
    ):
        await connection.execute(sa.text("""
            INSERT INTO skills (id, subtopic_id, code, name, normalized_name, status)
            VALUES (:id, :subtopic, :code, :name, :normalized, 'active')
        """), {"id": ids[key], "subtopic": ids["subtopic"], "code": code,
                "name": name, "normalized": normalized})
    if target:
        await connection.execute(sa.text("""
            INSERT INTO topics (subject_id, grade_id, code, name, normalized_name, status)
            VALUES (:subject, :grade, 'canonical-target',
                    'Движение и взаимодействие тел',
                    'движение и взаимодействие тел', 'active')
        """), {"subject": ids["subject"], "grade": ids["grade"]})
    if extra_subtopic:
        await connection.execute(sa.text("""
            INSERT INTO subtopics (topic_id, code, name, normalized_name, status)
            VALUES (:topic, 'custom', 'Пользовательская тема',
                    'пользовательская тема', 'active')
        """), {"topic": ids["topic"]})
    if task_subtopic != "none":
        ids["task"] = uuid4()
        subtopic_id = ids["subtopic"] if task_subtopic == "precise" else None
        await connection.execute(sa.text("""
            INSERT INTO tasks (id, subject_id, grade_id, topic_id, subtopic_id, created_by)
            VALUES (:id, :subject, :grade, :topic, :subtopic, :created_by)
        """), {"id": ids["task"], "subject": ids["subject"],
                "grade": ids["grade"], "topic": ids["topic"],
                "subtopic": subtopic_id, "created_by": uuid4()})
    return ids


async def test_historical_starter_and_precise_task_keep_every_identity():
    engine = create_async_engine(URL)
    async with engine.begin() as connection:
        ids = await insert_starter(connection, task_subtopic="precise")
        before = (await connection.execute(sa.text(
            "SELECT code FROM topics WHERE id=:id"), {"id": ids["topic"]})).scalar_one()
    alembic("upgrade", REVISION)
    async with engine.connect() as connection:
        topic = (await connection.execute(sa.text(
            "SELECT id, code, name FROM topics WHERE id=:id"),
            {"id": ids["topic"]})).one()
        assert topic == (ids["topic"], before, "Движение и взаимодействие тел")
        assert (await connection.execute(sa.text(
            "SELECT id FROM subtopics WHERE topic_id=:id"),
            {"id": ids["topic"]})).scalar_one() == ids["subtopic"]
        assert set((await connection.execute(sa.text(
            "SELECT id FROM skills WHERE subtopic_id=:id"),
            {"id": ids["subtopic"]})).scalars()) == {ids["speed"], ids["graph"]}
        assert (await connection.execute(sa.text(
            "SELECT topic_id, subtopic_id FROM tasks WHERE id=:id"),
            {"id": ids["task"]})).one() == (ids["topic"], ids["subtopic"])
        assert (await connection.execute(sa.text("""
            SELECT count(*) FROM topics WHERE subject_id=:subject AND grade_id=:grade
              AND status IN ('active','provisional') AND normalized_name='механика'
        """), {"subject": ids["subject"], "grade": ids["grade"]})).scalar_one() == 0
    await engine.dispose()


async def test_clean_database_is_a_noop():
    alembic("upgrade", REVISION)
    engine = create_async_engine(URL)
    async with engine.connect() as connection:
        assert (await connection.execute(sa.text(
            "SELECT count(*) FROM subjects WHERE normalized_name='физика'"))).scalar_one() == 0
    await engine.dispose()


@pytest.mark.parametrize("variant", ["target", "extra", "broad"])
async def test_modified_or_semantically_unsafe_starter_is_blocked(variant):
    engine = create_async_engine(URL)
    async with engine.begin() as connection:
        await insert_starter(connection, target=variant == "target",
                             extra_subtopic=variant == "extra",
                             task_subtopic="broad" if variant == "broad" else "none")
    result = alembic("upgrade", REVISION, succeeds=False)
    expected = {"target": "canonical target", "extra": "exactly one untouched Subtopic",
                "broad": "broad or unexpected historical Topic reference"}[variant]
    assert expected in result.stderr
    await engine.dispose()


async def test_seed_reuses_bridge_then_is_fully_idempotent():
    engine = create_async_engine(URL)
    async with engine.begin() as connection:
        ids = await insert_starter(connection)
    alembic("upgrade", REVISION)
    environment = os.environ.copy()
    environment["DATABASE_URL"] = URL
    first = subprocess.run(["python", "-m", "app.tools.seed_school_catalog"],
                           cwd=BACKEND, env=environment, check=True,
                           capture_output=True, text=True)
    second = subprocess.run(["python", "-m", "app.tools.seed_school_catalog"],
                            cwd=BACKEND, env=environment, check=True,
                            capture_output=True, text=True)
    assert "topics: created=" in first.stdout
    assert all("created=0" in line for line in second.stdout.splitlines())
    async with engine.connect() as connection:
        physics = (await connection.execute(sa.text("""
            SELECT t.id, t.name FROM topics t JOIN subjects s ON s.id=t.subject_id
            JOIN grades g ON g.id=t.grade_id
            WHERE s.normalized_name='физика' AND g.number=7
        """))).all()
        assert physics == [(ids["topic"], "Движение и взаимодействие тел")]
    await engine.dispose()
