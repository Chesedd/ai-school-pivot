"""Source contract for the canonical Mathematics grades 10–11 curriculum."""
import hashlib
import json
from pathlib import Path

from app.infrastructure.models import normalize_catalog_name

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
EXPECTED_TOPICS = {
    10: {"Числа и вычисления", "Уравнения и неравенства", "Функции и графики",
         "Начала математического анализа", "Множества и логика", "Геометрия",
         "Вероятность и статистика"},
    11: {"Числа и вычисления", "Уравнения и неравенства", "Функции и графики",
         "Начала математического анализа", "Геометрия", "Вероятность и статистика"},
}
EXPECTED_COUNTS = {10: (7, 103, 230), 11: (6, 79, 168)}
PRESERVED_FINGERPRINTS = {
    1: "63b3e1867de3494be0f47e3d3287ec6c8ab8c9d41beeb1b92d4cf178d116cb83",
    2: "340d46bca3f77d3d9efce5b0dfb007f3df7cd3b6abfa7347b2a9882d80d9668d",
    3: "b1e872d134b6ded76cb58aa5b9cc7614e34e7500049074dfd9c1e47394c3e058",
    4: "a59dbca77ada396925e365eb0325489db84b9a96fa29de78faa56aa113cd1fc0",
    5: "e4ea5936aa3cebdc35072499bc9907201790649f245a8b935a4c886ae39cedad",
    6: "1f87d67644a1f9536440fbc24c61a029cc6c931455f86ca81405e4d8cd26b65a",
    7: "93022fe5bdd9d7f483b946875a88caac52d1efd2340a5d8bccdae591b8d1545c",
    8: "fdf15fbfc984c5b41473beb743b499b8730c3a846fe6079984d7d0608400c8ed",
    9: "40e1aaedd102a99388e010a389ae5386fd7685a3005a514972a4b93d4029f0b4",
}
REPRESENTATIVE_NAMES = {
    "Логарифмическая функция", "Производная функции", "Определённый интеграл",
    "Сфера и шар", "Нормальное распределение",
}


def _fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _assert_unique(names):
    normalized = [normalize_catalog_name(name) for name in names]
    assert len(normalized) == len(set(normalized))


def test_mathematics_10_11_source_contract_and_normalized_uniqueness():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    mathematics = [subject for subject in source["subjects"] if subject["name"] == "Математика"]
    assert len(mathematics) == 1
    grades_list = mathematics[0]["grades"]
    assert [grade["number"] for grade in grades_list] == list(range(1, 12))
    grades = {grade["number"]: grade for grade in grades_list}

    seen_representatives = set()
    for number, expected_topics in EXPECTED_TOPICS.items():
        topics = grades[number]["topics"]
        assert {topic["name"] for topic in topics} == expected_topics
        counts = (len(topics), sum(len(topic["subtopics"]) for topic in topics),
                  sum(len(subtopic["skills"]) for topic in topics
                      for subtopic in topic["subtopics"]))
        assert counts == EXPECTED_COUNTS[number]
        _assert_unique(topic["name"] for topic in topics)
        for topic in topics:
            _assert_unique(subtopic["name"] for subtopic in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                _assert_unique(subtopic["skills"])
                if subtopic["name"] in REPRESENTATIVE_NAMES:
                    seen_representatives.add(subtopic["name"])
                    assert normalize_catalog_name(subtopic["name"]) == normalize_catalog_name(
                        subtopic["name"])
    assert seen_representatives == REPRESENTATIVE_NAMES


def test_mathematics_grades_1_9_are_semantically_unchanged():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    mathematics = next(subject for subject in source["subjects"]
                       if subject["name"] == "Математика")
    grades = {grade["number"]: grade for grade in mathematics["grades"]}
    assert {number: _fingerprint(grades[number]) for number in PRESERVED_FINGERPRINTS} \
        == PRESERVED_FINGERPRINTS


def test_mathematical_symbol_normalization_remains_distinct():
    assert normalize_catalog_name("Функция y = √x") != normalize_catalog_name("Функция y = |x|")
