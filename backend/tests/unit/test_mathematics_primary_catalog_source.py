"""Contract tests for the versioned Mathematics grades 1–4 curriculum source."""
import json
import re
import unicodedata
from pathlib import Path

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"


def normalize_catalog_name(value):
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")).strip()

TOPICS = {
    "Числа и величины",
    "Арифметические действия",
    "Текстовые задачи",
    "Пространственные отношения и геометрические фигуры",
    "Математическая информация",
}
EXPECTED_COUNTS = {
    1: (5, 11, 38),
    2: (5, 14, 58),
    3: (5, 18, 61),
    4: (5, 23, 81),
}
GRADE_7_STARTER = {
    "Уравнения": {
        "Линейные уравнения": ["Решать линейные уравнения", "Проверять корни уравнения"],
        "Квадратные уравнения": ["Решать квадратные уравнения", "Применять дискриминант"],
    },
    "Функции": {"Линейная функция": ["Строить график линейной функции", "Определять коэффициенты функции"]},
    "Геометрия": {"Треугольники": ["Применять признаки равенства треугольников", "Вычислять углы треугольника"]},
}


def _unique_normalized(names):
    normalized = [normalize_catalog_name(name) for name in names]
    assert len(normalized) == len(set(normalized))


def test_mathematics_primary_taxonomy_is_complete_unique_and_grade_scoped():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    mathematics = [subject for subject in source["subjects"] if subject["name"] == "Математика"]
    assert len(mathematics) == 1
    grades = {grade["number"]: grade for grade in mathematics[0]["grades"]}
    assert {1, 2, 3, 4, 7} <= grades.keys()

    for number, expected_counts in EXPECTED_COUNTS.items():
        topics = grades[number]["topics"]
        assert {topic["name"] for topic in topics} == TOPICS
        assert (len(topics), sum(len(topic["subtopics"]) for topic in topics),
                sum(len(subtopic["skills"]) for topic in topics
                    for subtopic in topic["subtopics"])) == expected_counts
        _unique_normalized(topic["name"] for topic in topics)
        for topic in topics:
            _unique_normalized(subtopic["name"] for subtopic in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                _unique_normalized(subtopic["skills"])


def test_mathematics_grade_7_starter_taxonomy_is_unchanged():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    mathematics = next(subject for subject in source["subjects"] if subject["name"] == "Математика")
    grade_7 = next(grade for grade in mathematics["grades"] if grade["number"] == 7)
    actual = {topic["name"]: {subtopic["name"]: subtopic["skills"]
        for subtopic in topic["subtopics"]} for topic in grade_7["topics"]}
    assert actual == GRADE_7_STARTER
