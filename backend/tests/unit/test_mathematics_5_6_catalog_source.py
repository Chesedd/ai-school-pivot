"""Source contract for the canonical Mathematics grades 5–6 curriculum."""
import hashlib
import json
import re
import unicodedata
from pathlib import Path


DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
EXPECTED_TOPICS = {
    5: {
        "Натуральные числа и нуль",
        "Дроби",
        "Решение текстовых задач",
        "Наглядная геометрия",
    },
    6: {
        "Натуральные числа",
        "Дроби",
        "Положительные и отрицательные числа",
        "Буквенные выражения",
        "Решение текстовых задач",
        "Наглядная геометрия",
    },
}
EXPECTED_COUNTS = {5: (4, 29, 116), 6: (6, 36, 135)}
PRESERVED_GRADE_FINGERPRINTS = {
    1: "63b3e1867de3494be0f47e3d3287ec6c8ab8c9d41beeb1b92d4cf178d116cb83",
    2: "340d46bca3f77d3d9efce5b0dfb007f3df7cd3b6abfa7347b2a9882d80d9668d",
    3: "b1e872d134b6ded76cb58aa5b9cc7614e34e7500049074dfd9c1e47394c3e058",
    4: "a59dbca77ada396925e365eb0325489db84b9a96fa29de78faa56aa113cd1fc0",
}


def _normalized(value):
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return re.sub(r"[^\w]+", " ", value).strip()


def _assert_unique(names):
    normalized = [_normalized(name) for name in names]
    assert len(normalized) == len(set(normalized))


def _fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def test_mathematics_5_6_taxonomy_is_complete_unique_and_grade_scoped():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    mathematics = [item for item in source["subjects"] if item["name"] == "Математика"]
    assert len(mathematics) == 1
    grades = {grade["number"]: grade for grade in mathematics[0]["grades"]}
    assert set(grades) == set(range(1, 10))

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


def test_preexisting_mathematics_grades_are_byte_semantically_unchanged():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    mathematics = next(item for item in source["subjects"] if item["name"] == "Математика")
    grades = {grade["number"]: grade for grade in mathematics["grades"]}

    assert {number: _fingerprint(grades[number]) for number in PRESERVED_GRADE_FINGERPRINTS} \
        == PRESERVED_GRADE_FINGERPRINTS
