"""Contracts for the canonical Russian Language grades 5–6 source."""
import hashlib
import json
from pathlib import Path

from app.infrastructure.models import normalize_catalog_name

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
EXPECTED_TOPICS = {
    5: {
        "Общие сведения о языке", "Язык и речь", "Текст",
        "Функциональные разновидности языка", "Фонетика, графика и орфоэпия",
        "Лексикология", "Морфемика", "Морфология", "Синтаксис",
        "Орфография и пунктуация",
    },
    6: {
        "Общие сведения о языке", "Язык и речь", "Текст",
        "Функциональные разновидности языка", "Лексикология",
        "Словообразование", "Морфология", "Орфография и пунктуация",
    },
}
EXPECTED_COUNTS = {5: (10, 176, 217), 6: (8, 132, 149)}
PRESERVED_RUSSIAN_FINGERPRINTS = {
    1: "3ed15bd27c8d6c8931bca4c875282aa067807b6fa3063b849645388bc1353c98",
    2: "85f5ce177949b63f648cd9466013e654968af935f6d17b20bf7aa380f4254a87",
    3: "39d14547fe39e912948ad3fba57ba0128287c657d5c18ca907b9506f5ce56c8d",
    4: "ab9350654a9d35d15a5c7eb4d40176649b21dbaf7df2353abdfca472ee591aa2",
    7: "1e01f6456f8a9e41c2a522ba9bf2269204018586272d16f5360cac3cfb4ff188",
}
MATHEMATICS_FINGERPRINT = "1702f86692e556e5a92d148358aca8911124f77c5f8d955d35c88d284f41e66a"


def _fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _assert_unique(names):
    normalized = [normalize_catalog_name(name) for name in names]
    assert len(normalized) == len(set(normalized))


def _source():
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_russian_5_6_source_contract_and_normalized_uniqueness():
    source = _source()
    subjects = [item for item in source["subjects"] if item["name"] == "Русский язык"]
    assert len(subjects) == 1
    grades = {grade["number"]: grade for grade in subjects[0]["grades"]}
    assert set(grades) == {1, 2, 3, 4, 5, 6, 7}
    for number in (5, 6):
        topics = grades[number]["topics"]
        assert {topic["name"] for topic in topics} == EXPECTED_TOPICS[number]
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


def test_representative_russian_5_6_nodes_are_present():
    russian = next(item for item in _source()["subjects"]
                   if item["name"] == "Русский язык")
    grades = {grade["number"]: grade for grade in russian["grades"]}
    expected = {
        5: {
            "Фонетика, графика и орфоэпия": "Фонетический анализ слова",
            "Лексикология": "Паронимы", "Морфемика": "Морфемный анализ слова",
            "Морфология": "Имя существительное",
            "Синтаксис": "Тире между подлежащим и сказуемым",
            "Орфография и пунктуация": "-ТСЯ и -ТЬСЯ",
        },
        6: {
            "Лексикология": "Фразеологизмы",
            "Словообразование": "Словообразовательный анализ",
            "Морфология": {"Имя числительное", "Местоимение", "Наклонение глагола"},
            "Орфография и пунктуация": "ПРЕ- и ПРИ-",
        },
    }
    for number, nodes in expected.items():
        topics = {topic["name"]: topic for topic in grades[number]["topics"]}
        for topic_name, expected_names in nodes.items():
            actual = {item["name"] for item in topics[topic_name]["subtopics"]}
            if isinstance(expected_names, str):
                expected_names = {expected_names}
            assert expected_names <= actual


def test_preserved_catalog_trees_are_semantically_unchanged():
    source = _source()
    russian = next(item for item in source["subjects"] if item["name"] == "Русский язык")
    grades = {grade["number"]: grade for grade in russian["grades"]}
    assert {number: _fingerprint(grades[number])
            for number in PRESERVED_RUSSIAN_FINGERPRINTS} == PRESERVED_RUSSIAN_FINGERPRINTS
    mathematics = next(item for item in source["subjects"] if item["name"] == "Математика")
    assert _fingerprint(mathematics) == MATHEMATICS_FINGERPRINT


def test_grade_boundaries_and_russian_literature_boundary():
    russian = next(item for item in _source()["subjects"]
                   if item["name"] == "Русский язык")
    grades = {grade["number"]: grade for grade in russian["grades"]}
    names = lambda number: {name for topic in grades[number]["topics"]
                            for subtopic in topic["subtopics"]
                            for name in (topic["name"], subtopic["name"], *subtopic["skills"])}
    grade_5_names = names(5)
    assert not any(term in name.casefold() for name in grade_5_names for term in (
        "имя числительное", "местоимение как часть речи", "наклонение глагола",
        "безличный глагол", "разноспрягаемый глагол", "причаст", "деепричаст"))
    all_new_names = grade_5_names | names(6)
    forbidden = ("автор произведения", "литературный герой", "сюжет произведения",
                 "жанр художественного произведения", "характеристика персонажа",
                 "литературоведческий анализ")
    assert not any(term in name.casefold() for name in all_new_names for term in forbidden)
