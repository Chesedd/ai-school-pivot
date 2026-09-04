"""Contracts for the canonical Russian Language grades 1–4 source."""
import hashlib
import json
from pathlib import Path

from app.infrastructure.models import normalize_catalog_name

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
GRADE_1_TOPICS = {
    "Сведения о русском языке", "Обучение письму", "Фонетика и графика",
    "Лексика", "Синтаксис", "Орфография и пунктуация", "Развитие речи",
}
UPPER_PRIMARY_TOPICS = {
    "Сведения о русском языке", "Фонетика и графика", "Лексика",
    "Состав слова (морфемика)", "Морфология", "Синтаксис",
    "Орфография и пунктуация", "Развитие речи",
}
EXPECTED_COUNTS = {
    1: (7, 46, 72), 2: (8, 55, 80), 3: (8, 65, 82), 4: (8, 74, 94),
}
GRADE_7_FINGERPRINT = "1e01f6456f8a9e41c2a522ba9bf2269204018586272d16f5360cac3cfb4ff188"
MATHEMATICS_FINGERPRINT = "1702f86692e556e5a92d148358aca8911124f77c5f8d955d35c88d284f41e66a"


def _fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _assert_unique(names):
    normalized = [normalize_catalog_name(name) for name in names]
    assert len(normalized) == len(set(normalized))


def test_russian_primary_source_contract_and_normalized_uniqueness():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    subjects = [subject for subject in source["subjects"]
                if subject["name"] == "Русский язык"]
    assert len(subjects) == 1
    grades = {grade["number"]: grade for grade in subjects[0]["grades"]}
    assert set(grades) == {1, 2, 3, 4, 5, 6, 7}

    for number in range(1, 5):
        topics = grades[number]["topics"]
        assert {topic["name"] for topic in topics} == (
            GRADE_1_TOPICS if number == 1 else UPPER_PRIMARY_TOPICS)
        counts = (len(topics), sum(len(topic["subtopics"]) for topic in topics),
                  sum(len(subtopic["skills"]) for topic in topics
                      for subtopic in topic["subtopics"]))
        assert counts == EXPECTED_COUNTS[number]
        _assert_unique(topic["name"] for topic in topics)
        for topic in topics:
            _assert_unique(subtopic["name"] for subtopic in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                assert subtopic["skills"]
                _assert_unique(subtopic["skills"])


def test_representative_russian_primary_nodes_are_present():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    russian = next(subject for subject in source["subjects"]
                   if subject["name"] == "Русский язык")
    grades = {grade["number"]: grade for grade in russian["grades"]}
    expected = {
        1: {"Фонетика и графика": "Слог и ударение",
            "Орфография и пунктуация": "ЖИ–ШИ"},
        2: {"Состав слова (морфемика)": "Однокоренные слова",
            "Орфография и пунктуация": "Безударные гласные в корне слова"},
        3: {"Морфология": "Падеж имён существительных",
            "Синтаксис": "Главные члены предложения"},
        4: {"Морфология": "Спряжение глагола",
            "Орфография и пунктуация": "-ТСЯ и -ТЬСЯ"},
    }
    for number, nodes in expected.items():
        topics = {topic["name"]: topic for topic in grades[number]["topics"]}
        for topic_name, subtopic_name in nodes.items():
            assert subtopic_name in {item["name"] for item in topics[topic_name]["subtopics"]}


def test_historical_russian_grade_7_and_mathematics_are_semantically_unchanged():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    russian = next(subject for subject in source["subjects"]
                   if subject["name"] == "Русский язык")
    mathematics = next(subject for subject in source["subjects"]
                       if subject["name"] == "Математика")
    grade_7 = next(grade for grade in russian["grades"] if grade["number"] == 7)
    assert _fingerprint(grade_7) == GRADE_7_FINGERPRINT
    assert _fingerprint(mathematics) == MATHEMATICS_FINGERPRINT


def test_russian_primary_respects_literary_reading_boundary():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    russian = next(subject for subject in source["subjects"]
                   if subject["name"] == "Русский язык")
    names = [name for grade in russian["grades"] if grade["number"] in range(1, 5)
             for topic in grade["topics"] for subtopic in topic["subtopics"]
             for name in (topic["name"], subtopic["name"], *subtopic["skills"])]
    forbidden = ("автор произведения", "литературный герой", "сюжет произведения",
                 "выразительное чтение произведения")
    assert not any(term in name.casefold() for term in forbidden for name in names)
