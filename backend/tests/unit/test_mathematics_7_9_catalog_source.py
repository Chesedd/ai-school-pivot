"""Source contract for the canonical Mathematics grades 7–9 curriculum."""
import hashlib
import json
import re
import unicodedata
from pathlib import Path

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
EXPECTED_TOPICS = {
    7: {"Числа и вычисления", "Алгебраические выражения", "Уравнения", "Функции", "Геометрия", "Вероятность и статистика"},
    8: {"Числа и вычисления", "Алгебраические выражения", "Уравнения и неравенства", "Функции", "Геометрия", "Вероятность и статистика"},
    9: {"Числа и вычисления", "Уравнения и неравенства", "Функции", "Числовые последовательности и прогрессии", "Геометрия", "Вероятность и статистика"},
}
EXPECTED_COUNTS = {7: (6, 49, 134), 8: (6, 57, 126), 9: (6, 71, 144)}
PRESERVED_FINGERPRINTS = {
    1: "63b3e1867de3494be0f47e3d3287ec6c8ab8c9d41beeb1b92d4cf178d116cb83",
    2: "340d46bca3f77d3d9efce5b0dfb007f3df7cd3b6abfa7347b2a9882d80d9668d",
    3: "b1e872d134b6ded76cb58aa5b9cc7614e34e7500049074dfd9c1e47394c3e058",
    4: "a59dbca77ada396925e365eb0325489db84b9a96fa29de78faa56aa113cd1fc0",
    5: "e4ea5936aa3cebdc35072499bc9907201790649f245a8b935a4c886ae39cedad",
    6: "1f87d67644a1f9536440fbc24c61a029cc6c931455f86ca81405e4d8cd26b65a",
}
HISTORICAL_GRADE_7 = {
    "Уравнения": {
        "Линейные уравнения": {"Решать линейные уравнения", "Проверять корни уравнения"},
        "Квадратные уравнения": {"Решать квадратные уравнения", "Применять дискриминант"},
    },
    "Функции": {"Линейная функция": {"Строить график линейной функции", "Определять коэффициенты функции"}},
    "Геометрия": {"Треугольники": {"Применять признаки равенства треугольников", "Вычислять углы треугольника"}},
}

def normalized(value):
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    value = value.replace("√", " sqrt ").replace("|", " abs ")
    return re.sub(r"[^\w]+", " ", value).strip()

def fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()

def assert_unique(names):
    values = [normalized(name) for name in names]
    assert len(values) == len(set(values))

def test_mathematics_7_9_source_contract_and_normalized_uniqueness():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    mathematics = [subject for subject in source["subjects"] if subject["name"] == "Математика"]
    assert len(mathematics) == 1
    grades = {grade["number"]: grade for grade in mathematics[0]["grades"]}
    assert set(grades) == set(range(1, 12))
    for number, names in EXPECTED_TOPICS.items():
        topics = grades[number]["topics"]
        assert {topic["name"] for topic in topics} == names
        assert (len(topics), sum(len(t["subtopics"]) for t in topics),
                sum(len(s["skills"]) for t in topics for s in t["subtopics"])) == EXPECTED_COUNTS[number]
        assert_unique(t["name"] for t in topics)
        for topic in topics:
            assert_unique(s["name"] for s in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                assert_unique(subtopic["skills"])

def test_grades_1_6_are_semantically_unchanged():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    mathematics = next(s for s in source["subjects"] if s["name"] == "Математика")
    grades = {grade["number"]: grade for grade in mathematics["grades"]}
    assert {n: fingerprint(grades[n]) for n in PRESERVED_FINGERPRINTS} == PRESERVED_FINGERPRINTS

def test_historical_grade_7_nodes_are_preserved_and_quadratics_not_expanded():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    mathematics = next(s for s in source["subjects"] if s["name"] == "Математика")
    grade = next(g for g in mathematics["grades"] if g["number"] == 7)
    topics = {topic["name"]: topic for topic in grade["topics"]}
    for topic_name, expected_subtopics in HISTORICAL_GRADE_7.items():
        subtopics = {sub["name"]: sub for sub in topics[topic_name]["subtopics"]}
        for subtopic_name, historical_skills in expected_subtopics.items():
            assert historical_skills <= set(subtopics[subtopic_name]["skills"])
    quadratics = next(s for s in topics["Уравнения"]["subtopics"] if s["name"] == "Квадратные уравнения")
    assert quadratics["skills"] == ["Решать квадратные уравнения", "Применять дискриминант"]
