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

from app.application.catalog_options import CatalogOptionQuery, CatalogOptionService  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.tools.seed_school_catalog import DATA, seed_catalog  # noqa: E402

pytestmark = pytest.mark.asyncio

CANONICAL_SUBJECTS = {
    "Русский язык", "Литературное чтение", "Литература", "Математика",
    "Окружающий мир", "Информатика", "Физика", "Химия", "Биология",
    "История", "Обществознание", "География", "Английский язык",
}

NON_CANONICAL_TOP_LEVEL_NAMES = {
    "Алгебра", "Геометрия", "Вероятность и статистика", "История России",
    "Всеобщая история", "Иностранный язык", "Изобразительное искусство",
    "Музыка", "Труд (технология)", "Физическая культура",
    "Основы безопасности и защиты Родины",
    "Основы религиозных культур и светской этики", "Родной язык",
    "Родная литература",
}


@pytest_asyncio.fixture(autouse=True)
async def clean():
    await engine.dispose()
    async with async_session_factory() as db, db.begin():
        await db.execute(text("TRUNCATE skills, subtopics, topics, grades, subjects, users CASCADE"))
    yield
    try:
        async with async_session_factory() as db, db.begin():
            await db.execute(text("TRUNCATE skills, subtopics, topics, grades, subjects, users CASCADE"))
    finally:
        await engine.dispose()


async def test_full_seed_first_run_and_second_run_are_complete_and_idempotent():
    source_subjects = {item["name"] for item in json.loads(DATA.read_text())["subjects"]}
    assert source_subjects == CANONICAL_SUBJECTS
    assert source_subjects.isdisjoint(NON_CANONICAL_TOP_LEVEL_NAMES)

    first = await seed_catalog(session_factory=async_session_factory)
    assert first["grades"]["created"] == 11
    assert first["subjects"] == {"created": 13, "reused": 0, "conflicts": 0}
    assert all(first[kind]["created"] > 0 for kind in ("subjects", "topics", "subtopics", "skills"))
    async with async_session_factory() as db:
        before = {table: await db.scalar(text(f"SELECT count(*) FROM {table}"))
                  for table in ("grades", "subjects", "topics", "subtopics", "skills")}
        ids = {table: (await db.execute(text(f"SELECT id FROM {table} ORDER BY id"))).scalars().all()
               for table in ("grades", "subjects", "topics", "subtopics", "skills")}
        live_subjects = (await db.execute(text(
            "SELECT name FROM subjects WHERE status IN ('active', 'provisional')"
        ))).scalars().all()
        assert set(live_subjects) == CANONICAL_SUBJECTS
        assert len(live_subjects) == len(CANONICAL_SUBJECTS)
        for query, expected in {
            "матем": {"Математика"},
            "русск": {"Русский язык"},
            "литератур": {"Литература", "Литературное чтение"},
            "окруж": {"Окружающий мир"},
            "информ": {"Информатика"},
            "физ": {"Физика"},
            "обществ": {"Обществознание"},
            "англ": {"Английский язык"},
        }.items():
            result = await CatalogOptionService(db).search(
                CatalogOptionQuery("subjects", query, 20)
            )
            assert expected.intersection(item["name"] for item in result["items"])
    second = await seed_catalog(session_factory=async_session_factory)
    assert all(second[kind]["created"] == 0 for kind in second)
    async with async_session_factory() as db:
        after = {table: await db.scalar(text(f"SELECT count(*) FROM {table}")) for table in before}
        assert {table: (await db.execute(text(f"SELECT id FROM {table} ORDER BY id"))).scalars().all()
                for table in ids} == ids
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


async def test_mathematics_hierarchy_search_and_metadata_resolution():
    """The seeded taxonomy stays grade-scoped and resolves without a provider."""
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    from app.application.image_solving_contracts import (ExtractionResultV1,
        ImageSolvingSession, ImageSolvingStatus)
    from app.application.image_solving_metadata import resolve_metadata
    from app.infrastructure.image_solving_metadata import SqlAlchemyMetadataCatalogLoader

    await seed_catalog(session_factory=async_session_factory)
    async with async_session_factory() as db:
        rows = (await db.execute(text("""
            SELECT g.number, count(DISTINCT t.id), count(DISTINCT st.id), count(DISTINCT sk.id)
            FROM subjects s JOIN topics t ON t.subject_id=s.id
            JOIN grades g ON g.id=t.grade_id
            JOIN subtopics st ON st.topic_id=t.id JOIN skills sk ON sk.subtopic_id=st.id
            WHERE s.normalized_name='математика' AND g.number BETWEEN 1 AND 4
            GROUP BY g.number ORDER BY g.number
        """))).all()
        assert rows == [(1, 5, 11, 38), (2, 5, 14, 58), (3, 5, 18, 61), (4, 5, 23, 81)]
        expanded_rows = (await db.execute(text("""
            SELECT g.number, count(DISTINCT t.id), count(DISTINCT st.id), count(DISTINCT sk.id)
            FROM subjects s JOIN topics t ON t.subject_id=s.id
            JOIN grades g ON g.id=t.grade_id
            JOIN subtopics st ON st.topic_id=t.id JOIN skills sk ON sk.subtopic_id=st.id
            WHERE s.normalized_name='математика' AND g.number BETWEEN 5 AND 9
            GROUP BY g.number ORDER BY g.number
        """))).all()
        assert expanded_rows == [
            (5, 4, 29, 116), (6, 6, 36, 135), (7, 6, 49, 134),
            (8, 6, 57, 126), (9, 6, 71, 144),
        ]
        senior_rows = (await db.execute(text("""
            SELECT g.number, count(DISTINCT t.id), count(DISTINCT st.id), count(DISTINCT sk.id)
            FROM subjects s JOIN topics t ON t.subject_id=s.id
            JOIN grades g ON g.id=t.grade_id JOIN subtopics st ON st.topic_id=t.id
            JOIN skills sk ON sk.subtopic_id=st.id
            WHERE s.normalized_name='математика' AND g.number BETWEEN 10 AND 11
            GROUP BY g.number ORDER BY g.number
        """))).all()
        assert senior_rows == [(10, 7, 103, 230), (11, 6, 79, 168)]
        assert await db.scalar(text("""
            SELECT count(*) FROM topics t JOIN subjects s ON s.id=t.subject_id
            JOIN grades g ON g.id=t.grade_id WHERE s.normalized_name='математика'
            AND g.number BETWEEN 1 AND 4
        """)) == 20
        assert await db.scalar(text("""
            SELECT count(*) FROM skills sk JOIN subtopics st ON st.id=sk.subtopic_id
            JOIN topics t ON t.id=st.topic_id JOIN subjects s ON s.id=t.subject_id
            JOIN grades g ON g.id=t.grade_id WHERE s.normalized_name='математика'
            AND g.number BETWEEN 1 AND 4 AND t.grade_id != g.id
        """)) == 0

        subject_id = await db.scalar(text("SELECT id FROM subjects WHERE normalized_name='математика'"))
        grade_ids = dict((await db.execute(text("SELECT number,id FROM grades WHERE number BETWEEN 1 AND 11"))).all())
        service = CatalogOptionService(db)
        expected = {1: ("ариф", "Арифметические действия"),
                    2: ("умнож", "Умножение и деление"),
                    3: ("площад", "Площадь прямоугольника и квадрата"),
                    4: ("движ", "Задачи на движение")}
        for number, (query, name) in expected.items():
            if number == 1:
                result = await service.search(CatalogOptionQuery(
                    "topics", query, 20, subject_id, grade_ids[number]))
            else:
                topic_name = "Текстовые задачи" if number == 4 else (
                    "Пространственные отношения и геометрические фигуры" if number == 3
                    else "Арифметические действия")
                topic_id = await db.scalar(text("""
                    SELECT t.id FROM topics t JOIN grades g ON g.id=t.grade_id
                    WHERE t.subject_id=:subject AND g.number=:grade AND t.name=:name
                """), {"subject": subject_id, "grade": number, "name": topic_name})
                result = await service.search(CatalogOptionQuery(
                    "subtopics", query, 20, topic_id=topic_id))
            assert name in {item["name"] for item in result["items"]}

        grade_3_text_topic = await db.scalar(text("""
            SELECT t.id FROM topics t JOIN grades g ON g.id=t.grade_id
            WHERE t.subject_id=:subject AND g.number=3 AND t.name='Текстовые задачи'
        """), {"subject": subject_id})
        wrong_grade = await service.search(CatalogOptionQuery(
            "subtopics", "движ", 20, topic_id=grade_3_text_topic))
        assert "Задачи на движение" not in {item["name"] for item in wrong_grade["items"]}

        searches = [
            (5, "Натуральные числа и нуль", "делител", "Делители и кратные"),
            (5, "Дроби", "десятич", "Десятичные дроби"),
            (5, "Наглядная геометрия", "объём", "Объём"),
            (6, "Дроби", "процент", "Проценты"),
            (6, "Положительные и отрицательные числа", "координат", "Координатная плоскость"),
            (6, "Наглядная геометрия", "симмет", "Симметрия"),
        ]
        for number, topic_name, query, expected_name in searches:
            topic_id = await db.scalar(text("""
                SELECT id FROM topics WHERE subject_id=:subject AND grade_id=:grade AND name=:name
            """), {"subject": subject_id, "grade": grade_ids[number], "name": topic_name})
            result = await service.search(CatalogOptionQuery(
                "subtopics", query, 20, topic_id=topic_id))
            assert expected_name in {item["name"] for item in result["items"]}

        secondary_searches = [
            (7, "Алгебраические выражения", "subtopics", "многочлен", "Многочлены"),
            (7, "Геометрия", "subtopics", "треуг", "Треугольники"),
            (7, "Вероятность и статистика", "skills", "медиан", "Находить медиану"),
            (8, "Уравнения и неравенства", "skills", "дискрим", "Вычислять дискриминант"),
            (8, "Геометрия", "subtopics", "пифаг", "Теорема Пифагора"),
            (8, "Вероятность и статистика", "subtopics", "условн", "Условная вероятность"),
            (9, "Числовые последовательности и прогрессии", "subtopics", "прогресс", "Арифметическая прогрессия"),
            (9, "Геометрия", "subtopics", "косинус", "Теорема косинусов"),
            (9, "Вероятность и статистика", "subtopics", "бернул", "Испытания Бернулли"),
        ]
        for number, topic_name, kind, query, expected_name in secondary_searches:
            topic_id = await db.scalar(text("""
                SELECT id FROM topics WHERE subject_id=:subject AND grade_id=:grade AND name=:name
            """), {"subject": subject_id, "grade": grade_ids[number], "name": topic_name})
            kwargs = {"topic_id": topic_id}
            if kind == "skills":
                subtopic_id = await db.scalar(text("""
                    SELECT st.id FROM subtopics st JOIN skills sk ON sk.subtopic_id=st.id
                    WHERE st.topic_id=:topic AND sk.normalized_name LIKE :query
                    ORDER BY st.normalized_name LIMIT 1
                """), {"topic": topic_id, "query": f"%{query}%"})
                kwargs = {"subtopic_id": subtopic_id}
            result = await service.search(CatalogOptionQuery(kind, query, 20, **kwargs))
            assert expected_name in {item["name"] for item in result["items"]}

        senior_searches = [
            (10, "Уравнения и неравенства", "интервал", "Метод интервалов"),
            (10, "Начала математического анализа", "прогресс", "Арифметическая прогрессия"),
            (10, "Геометрия", "скрещ", "Скрещивающиеся прямые"),
            (10, "Вероятность и статистика", "бернул", "Испытания Бернулли"),
            (11, "Числа и вычисления", "логарифм", "Логарифм числа"),
            (11, "Начала математического анализа", "производн", "Производная функции"),
            (11, "Начала математического анализа", "интеграл", "Определённый интеграл"),
            (11, "Геометрия", "цилиндр", "Цилиндр"),
            (11, "Вероятность и статистика", "математическ ожид", "Математическое ожидание"),
        ]
        for number, topic_name, query, expected_name in senior_searches:
            topic_id = await db.scalar(text("""
                SELECT id FROM topics WHERE subject_id=:subject AND grade_id=:grade AND name=:name
            """), {"subject": subject_id, "grade": grade_ids[number], "name": topic_name})
            result = await service.search(CatalogOptionQuery("subtopics", query, 20,
                                                              topic_id=topic_id))
            assert expected_name in {item["name"] for item in result["items"]}

        grade_10_topics = await service.search(CatalogOptionQuery(
            "topics", "логарифмическое уравнение", 20, subject_id, grade_ids[10]))
        assert not grade_10_topics["items"]
        grade_11_geometry = await db.scalar(text("""
            SELECT id FROM topics WHERE subject_id=:subject AND grade_id=:grade
            AND name='Геометрия'
        """), {"subject": subject_id, "grade": grade_ids[11]})
        grade_11_leakage = await service.search(CatalogOptionQuery(
            "subtopics", "многогран", 20, topic_id=grade_11_geometry))
        assert not grade_11_leakage["items"]

        grade_7_equations = await db.scalar(text("""
            SELECT id FROM topics WHERE subject_id=:subject AND grade_id=:grade
            AND name='Уравнения'
        """), {"subject": subject_id, "grade": grade_ids[7]})
        legacy_quadratics = await db.scalar(text("""
            SELECT id FROM subtopics WHERE topic_id=:topic AND name='Квадратные уравнения'
        """), {"topic": grade_7_equations})
        grade_7_discriminant = await service.search(CatalogOptionQuery(
            "skills", "дискрим", 20, subtopic_id=legacy_quadratics))
        assert {item["name"] for item in grade_7_discriminant["items"]} == {
            "Применять дискриминант"}

        grade_5_topics = await service.search(CatalogOptionQuery(
            "topics", "отрицатель", 20, subject_id, grade_ids[5]))
        assert "Положительные и отрицательные числа" not in {
            item["name"] for item in grade_5_topics["items"]}

        snapshot = await SqlAlchemyMetadataCatalogLoader(db).load()
        extraction = ExtractionResultV1(extracted_text="Автомобиль проехал 120 км",
            structured_statement="Найти скорость автомобиля.", detected_task_type="problem",
            detected_answer_format="number", choices=None, extraction_confidence=Decimal(".99"),
            ocr_issues=(), metadata={"title":"Задача на движение", "subject":"Математика",
                "grade":4, "topic":"Текстовые задачи", "subtopic":"Задачи на движение",
                "skills":("Находить скорость",), "task_type":"problem",
                "answer_format":"number", "difficulty":2, "tags":()})
        now = datetime.now(UTC)
        session = ImageSolvingSession(session_id=uuid4(), owner_id=uuid4(),
            input_artifact_id=uuid4(), extraction_checkpoint=extraction,
            lifecycle_status=ImageSolvingStatus.VALIDATED, created_at=now, updated_at=now)
        recommendation = resolve_metadata(session, snapshot)
        assert recommendation.subject.label == "Математика"
        assert recommendation.grade.label == "4"
        assert recommendation.topic.label == "Текстовые задачи"
        assert recommendation.subtopic.label == "Задачи на движение"
        assert recommendation.skills[0].label == "Находить скорость"

        for number, topic, subtopic, skill in (
            (5, "Дроби", "Десятичные дроби", "Сравнивать десятичные дроби"),
            (6, "Дроби", "Проценты", "Находить процент от величины"),
            (10, "Уравнения и неравенства", "Тригонометрические уравнения",
             "Решать простейшие тригонометрические уравнения"),
            (10, "Геометрия", "Прямые и плоскости в пространстве",
             "Применять признаки взаимного расположения прямых и плоскостей"),
            (11, "Начала математического анализа", "Производная функции",
             "Находить производную функции"),
            (11, "Вероятность и статистика", "Математическое ожидание",
             "Находить математическое ожидание по распределению"),
        ):
            extraction_5_6 = extraction.model_copy(update={"metadata": {
                **extraction.metadata, "grade": number, "topic": topic,
                "subtopic": subtopic, "skills": (skill,),
            }})
            resolved = resolve_metadata(session.model_copy(
                update={"extraction_checkpoint": extraction_5_6}), snapshot)
            assert (resolved.grade.label, resolved.topic.label,
                    resolved.subtopic.label, resolved.skills[0].label) == (
                        str(number), topic, subtopic, skill)
