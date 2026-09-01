"""Idempotent demo/dev catalog seeding command; no ORM models are used."""

import asyncio

from sqlalchemy import text

from app.db.session import async_session_factory
from app.infrastructure.models import normalize_catalog_name

GRADES = tuple((number, f"{number} класс") for number in range(1, 12))


async def seed() -> None:
    """Insert a small explicitly demo/dev-only catalog chain idempotently."""
    async with async_session_factory() as session:
        async with session.begin():
            for number, name in GRADES:
                await session.execute(
                    text(
                        "INSERT INTO grades (number, name, normalized_name) VALUES (:number, :name, :normalized_name) "
                        "ON CONFLICT (number) DO UPDATE SET name = EXCLUDED.name, normalized_name = EXCLUDED.normalized_name"
                    ),
                    {"number": number, "name": name, "normalized_name": normalize_catalog_name(name)},
                )
            await session.execute(
                text(
                    "INSERT INTO subjects (code, name, normalized_name) VALUES (:code, :name, :normalized_name) "
                    "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, normalized_name = EXCLUDED.normalized_name"
                ),
                {"code": "demo-informatics", "name": "Информатика (demo/dev)", "normalized_name": normalize_catalog_name("Информатика (demo/dev)")},
            )
            subject_id = await session.scalar(text("SELECT id FROM subjects WHERE code = 'demo-informatics'"))
            grade_id = await session.scalar(text("SELECT id FROM grades WHERE number = 7"))
            await session.execute(
                text(
                    "INSERT INTO topics (subject_id, grade_id, code, name, normalized_name) "
                    "VALUES (:subject_id, :grade_id, :code, :name, :normalized_name) "
                    "ON CONFLICT (subject_id, grade_id, code) DO UPDATE SET name = EXCLUDED.name, normalized_name = EXCLUDED.normalized_name"
                ),
                {"subject_id": subject_id, "grade_id": grade_id, "code": "demo-algorithms", "name": "Алгоритмы (demo/dev)", "normalized_name": normalize_catalog_name("Алгоритмы (demo/dev)")},
            )
            topic_id = await session.scalar(
                text("SELECT id FROM topics WHERE subject_id = :subject_id AND grade_id = :grade_id AND code = 'demo-algorithms'"),
                {"subject_id": subject_id, "grade_id": grade_id},
            )
            await session.execute(
                text(
                    "INSERT INTO subtopics (topic_id, code, name, normalized_name) VALUES (:topic_id, :code, :name, :normalized_name) "
                    "ON CONFLICT (topic_id, code) DO UPDATE SET name = EXCLUDED.name, normalized_name = EXCLUDED.normalized_name"
                ),
                {"topic_id": topic_id, "code": "demo-linear-algorithms", "name": "Линейные алгоритмы (demo/dev)", "normalized_name": normalize_catalog_name("Линейные алгоритмы (demo/dev)")},
            )
            subtopic_id = await session.scalar(
                text("SELECT id FROM subtopics WHERE topic_id = :topic_id AND code = 'demo-linear-algorithms'"),
                {"topic_id": topic_id},
            )
            await session.execute(
                text(
                    "INSERT INTO skills (subtopic_id, code, name, normalized_name) VALUES (:subtopic_id, :code, :name, :normalized_name) "
                    "ON CONFLICT (subtopic_id, code) DO UPDATE SET name = EXCLUDED.name, normalized_name = EXCLUDED.normalized_name"
                ),
                {"subtopic_id": subtopic_id, "code": "demo-linear-program", "name": "Составление линейной программы (demo/dev)", "normalized_name": normalize_catalog_name("Составление линейной программы (demo/dev)")},
            )


if __name__ == "__main__":
    asyncio.run(seed())
