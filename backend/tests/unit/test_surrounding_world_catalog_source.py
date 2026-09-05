"""Contracts for the canonical Surrounding World grades 1–4 source."""
import hashlib
import json
from pathlib import Path

import pytest

from app.infrastructure.models import normalize_catalog_name

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
TOPICS = {"Человек и общество", "Человек и природа", "Правила безопасной жизнедеятельности"}
EXPECTED_COUNTS = {1: (3, 52, 67), 2: (3, 83, 91), 3: (3, 102, 116), 4: (3, 96, 106)}
OTHER_SUBJECT_FINGERPRINTS = {
    "Математика": "1702f86692e556e5a92d148358aca8911124f77c5f8d955d35c88d284f41e66a",
    "Русский язык": "475b735e4707eb7a4f807d3154613287841c89f1687f9e401264624b882556c3",
    "Литература": "fbe9eb1fb3efe4c2ee98dda56104f9e329559e12ff7b426502a44ff83b138487",
    "Литературное чтение": "f33f125b5d6cd89cf90312676784a5344f9ade2d49e3b15533bf44df36e0a23d",
    "Английский язык": "f78ff1c654a28495a4dd942cf96c4517a06f74dd1a14171d7fe07f31b31e5914",
    "Информатика": "04a69e336a7d0fa4ab365a5ce3aeaad8edfb92a969051a0a060a93b686c9c6f9",
    "Физика": "f855c4eb243a8e6ddd58bf794179ca0bfa0fcdc7409d4125fc69a3ad0d379927",
    "Химия": "01d1839896c637d7ff1909025ac10d565019b7030e20f4363fc7e2f242a716b4",
    "Биология": "e18d814a73c1f95e35d3f3ff573c237d4f2f55214c21a2da49c3799a1dbf1027",
    "История": "2dfb745d109356d5e3a386bbed384b88437687b7097f5f854d4d581b7eddd95f",
    "Обществознание": "1f4a74c204823c1c1efb26f714d6a49eff0c0dee36c3b7b1d2d241721e54b038",
    "География": "4f851a42d3ff3958b392c55321cc45cfba747201913d5765d6f216da9a42fbd0",
}


def _source():
    return json.loads(DATA.read_text(encoding="utf-8"))


def _world():
    matches = [s for s in _source()["subjects"] if s["name"] == "Окружающий мир"]
    assert len(matches) == 1
    return matches[0]


def _fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _unique(names):
    normalized = [normalize_catalog_name(name) for name in names]
    assert len(normalized) == len(set(normalized))


def _grades():
    return {grade["number"]: grade for grade in _world()["grades"]}


def _search(grade_number, query):
    terms = normalize_catalog_name(query).split()
    return [subtopic["name"] for topic in _grades()[grade_number]["topics"]
            for subtopic in topic["subtopics"]
            if all(term in normalize_catalog_name(" ".join(
                (topic["name"], subtopic["name"], *subtopic["skills"])))
                   for term in terms)]


def test_exact_coverage_counts_and_normalized_uniqueness():
    grades = _grades()
    assert set(grades) == {1, 2, 3, 4}
    assert sum(len(g["topics"]) for g in grades.values()) == 12
    for number, grade in grades.items():
        assert {topic["name"] for topic in grade["topics"]} == TOPICS
        _unique(topic["name"] for topic in grade["topics"])
        for topic in grade["topics"]:
            _unique(subtopic["name"] for subtopic in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                assert subtopic["skills"]
                _unique(subtopic["skills"])
        assert (len(grade["topics"]),
                sum(len(t["subtopics"]) for t in grade["topics"]),
                sum(len(s["skills"]) for t in grade["topics"] for s in t["subtopics"])) == EXPECTED_COUNTS[number]


def test_every_other_subject_is_semantically_unchanged():
    actual = {s["name"]: _fingerprint(s) for s in _source()["subjects"]
              if s["name"] != "Окружающий мир"}
    assert actual == OTHER_SUBJECT_FINGERPRINTS


@pytest.mark.parametrize(("grade", "query", "expected"), [
    (1, "части растен", "Части растения"), (1, "пешеход", "Безопасность пешехода"),
    (2, "компас", "Компас"), (2, "красн книг", "Красная книга России"),
    (2, "родослов", "Родословная"), (3, "круговорот", "Круговорот воды"),
    (3, "цепи питан", "Цепи питания"), (3, "пищевар", "Пищеварительная система"),
    (3, "семейн бюджет", "Семейный бюджет"),
    (4, "конституц", "Конституция Российской Федерации"),
    (4, "лента врем", "Лента времени"), (4, "природн зон", "Природные зоны России"),
    (4, "велосипед", "Безопасность велосипедиста"),
])
def test_grade_scoped_source_search(grade, query, expected):
    assert expected in _search(grade, query)


def test_grade_scopes_do_not_leak():
    assert "Конституция Российской Федерации" not in _search(1, "конституц")
    assert "Круговорот воды" not in _search(2, "круговорот")
    assert "Безопасность велосипедиста" not in _search(3, "велосипед")
    assert "Пищеварительная система" not in _search(4, "пищевар")


def test_representative_hierarchy_and_curriculum_boundaries():
    required = {1: {"Части растения", "Безопасность пешехода"},
                2: {"Компас", "Красная книга России"},
                3: {"Круговорот воды", "Цепи питания", "Системы органов"},
                4: {"Лента времени", "Природные зоны России", "Безопасность велосипедиста"}}
    for number, names in required.items():
        actual = {s["name"] for t in _grades()[number]["topics"] for s in t["subtopics"]}
        assert names <= actual
    all_names = [name.casefold() for grade in _grades().values() for topic in grade["topics"]
                 for subtopic in topic["subtopics"]
                 for name in (topic["name"], subtopic["name"], *subtopic["skills"])]
    forbidden = ("работать в группе", "оценивать свой вклад", "литературный герой",
                 "сюжет", "авторская позиция художественного произведения")
    assert not any(term in name for term in forbidden for name in all_names)
