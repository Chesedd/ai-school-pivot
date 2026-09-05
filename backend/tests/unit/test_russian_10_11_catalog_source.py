"""Contracts for the canonical Russian Language grades 10–11 source."""
import hashlib
import json
from pathlib import Path

import pytest

from app.infrastructure.models import normalize_catalog_name

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
EXPECTED_TOPICS = {
    10: {"Общие сведения о языке", "Языковая норма и культура речи",
         "Фонетика и орфоэпия", "Лексикология и фразеология",
         "Морфемика и словообразование", "Морфология", "Орфография",
         "Речь и речевое общение", "Текст и информационно-смысловая переработка"},
    11: {"Общие сведения о языке", "Синтаксис и синтаксические нормы",
         "Пунктуация", "Функциональная стилистика и культура речи"},
}
PRESERVED_RUSSIAN_FINGERPRINTS = {
    1: "3ed15bd27c8d6c8931bca4c875282aa067807b6fa3063b849645388bc1353c98",
    2: "85f5ce177949b63f648cd9466013e654968af935f6d17b20bf7aa380f4254a87",
    3: "39d14547fe39e912948ad3fba57ba0128287c657d5c18ca907b9506f5ce56c8d",
    4: "ab9350654a9d35d15a5c7eb4d40176649b21dbaf7df2353abdfca472ee591aa2",
    5: "6f6d3f7ef6be2e542c5f77c7e9ec93ea10258ccc45a709462c042973d572bd32",
    6: "b2e3c1320ac26624a65b11eb3ff7708d938d2a568f07fbd9774b47026bd51acb",
    7: "78fbb11596fa826da21dea223214016b7923c00b1f30286e713b1a5cf842ab6f",
    8: "fb54f95396b3b6adcb7b0d686a9e7277c82cf5fdd1cf85c150288d0a74706c01",
    9: "7908bd509169991d1ea5d7f268c683fb9aeeb5c260ba8636284a4784804b5545",
}
OTHER_SUBJECT_FINGERPRINTS = {
    "Математика": "1702f86692e556e5a92d148358aca8911124f77c5f8d955d35c88d284f41e66a",
    "Литература": "fbe9eb1fb3efe4c2ee98dda56104f9e329559e12ff7b426502a44ff83b138487",
    "Английский язык": "f78ff1c654a28495a4dd942cf96c4517a06f74dd1a14171d7fe07f31b31e5914",
    "Информатика": "0d4bb7ede4c136b55d66799963d74c0bbe2c8a2cc01663b22fd4cad3c7d78023",
    "Физика": "f855c4eb243a8e6ddd58bf794179ca0bfa0fcdc7409d4125fc69a3ad0d379927",
    "Химия": "01d1839896c637d7ff1909025ac10d565019b7030e20f4363fc7e2f242a716b4",
    "Биология": "e18d814a73c1f95e35d3f3ff573c237d4f2f55214c21a2da49c3799a1dbf1027",
    "История": "2dfb745d109356d5e3a386bbed384b88437687b7097f5f854d4d581b7eddd95f",
    "Обществознание": "1f4a74c204823c1c1efb26f714d6a49eff0c0dee36c3b7b1d2d241721e54b038",
    "География": "4f851a42d3ff3958b392c55321cc45cfba747201913d5765d6f216da9a42fbd0",
    "Литературное чтение": "f33f125b5d6cd89cf90312676784a5344f9ade2d49e3b15533bf44df36e0a23d",
    "Окружающий мир": "a32a30c897f96597353ac00022fa8f7b494576ac24a9840ca94dbe154127e4aa",
}


def _source():
    return json.loads(DATA.read_text(encoding="utf-8"))


def _fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _unique(names):
    normalized = [normalize_catalog_name(name) for name in names]
    assert len(normalized) == len(set(normalized))


def _grades():
    russian = next(s for s in _source()["subjects"] if s["name"] == "Русский язык")
    return {g["number"]: g for g in russian["grades"]}


def test_source_shape_exact_topics_and_normalized_uniqueness():
    subjects = [s for s in _source()["subjects"] if s["name"] == "Русский язык"]
    assert len(subjects) == 1
    grades = {g["number"]: g for g in subjects[0]["grades"]}
    assert set(grades) == set(range(1, 12))
    for number in (10, 11):
        topics = grades[number]["topics"]
        assert {t["name"] for t in topics} == EXPECTED_TOPICS[number]
        _unique(t["name"] for t in topics)
        for topic in topics:
            _unique(s["name"] for s in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                assert subtopic["skills"]
                _unique(subtopic["skills"])


def test_prior_russian_grades_and_other_subjects_are_semantically_unchanged():
    source = _source()
    grades = _grades()
    assert {n: _fingerprint(grades[n]) for n in range(1, 10)} == PRESERVED_RUSSIAN_FINGERPRINTS
    others = {s["name"]: _fingerprint(s) for s in source["subjects"] if s["name"] != "Русский язык"}
    assert others == OTHER_SUBJECT_FINGERPRINTS


def test_representative_nodes_are_present():
    expected = {
        10: {"Фонетика и орфоэпия": {"Акцентологические нормы"},
             "Лексикология и фразеология": {"Паронимы"},
             "Морфология": {"Нормы употребления числительных"},
             "Орфография": {"Н и НН в разных частях речи"},
             "Текст и информационно-смысловая переработка": {"Инфографика"}},
        11: {"Синтаксис и синтаксические нормы": {
                 "Согласование сказуемого с подлежащим", "Нормы управления"},
             "Пунктуация": {"Сложное предложение с разными видами связи"},
             "Функциональная стилистика и культура речи": {
                 "Научный стиль", "Официально-деловой стиль"}},
    }
    grades = _grades()
    for number, expected_topics in expected.items():
        topics = {t["name"]: t for t in grades[number]["topics"]}
        for topic_name, subtopics in expected_topics.items():
            assert subtopics <= {s["name"] for s in topics[topic_name]["subtopics"]}


def _search_subtopics(grade_number, query):
    query_terms = normalize_catalog_name(query).split()
    matches = []
    for topic in _grades()[grade_number]["topics"]:
        for subtopic in topic["subtopics"]:
            haystack = normalize_catalog_name(" ".join((topic["name"], subtopic["name"], *subtopic["skills"])))
            if all(term in haystack for term in query_terms):
                matches.append(subtopic["name"])
    return matches


@pytest.mark.parametrize(("grade", "query", "expected"), [
    (10, "ударен", "Акцентологические нормы"), (10, "пароним", "Паронимы"),
    (10, "сочетаем", "Лексическая сочетаемость"),
    (10, "числитель", "Нормы употребления числительных"),
    (10, "нн", "Н и НН в разных частях речи"), (10, "инфограф", "Инфографика"),
    (11, "согласован сказуем", "Согласование сказуемого с подлежащим"),
    (11, "управлен", "Нормы управления"), (11, "парцел", "Парцелляция"),
    (11, "разными видами", "Сложное предложение с разными видами связи"),
    (11, "официально", "Официально-деловой стиль"),
    (11, "публицист", "Публицистический стиль"),
])
def test_grade_scoped_local_search(grade, query, expected):
    assert expected in _search_subtopics(grade, query)


def test_grade_scopes_and_language_boundaries_do_not_leak():
    assert "Парцелляция" not in _search_subtopics(10, "парцел")
    assert "Паронимы" not in _search_subtopics(11, "пароним")
    names = [name for number in (10, 11) for topic in _grades()[number]["topics"]
             for subtopic in topic["subtopics"]
             for name in (topic["name"], subtopic["name"], *subtopic["skills"])]
    forbidden = ("егэ", "задание 4", "задание 7", "задание 8", "задание 16",
                 "задание 26", "задание 27", "сюжет", "литературный герой",
                 "образ героя", "авторская позиция произведения", "идея произведения",
                 "литературное направление")
    assert not any(term in name.casefold() for name in names for term in forbidden)
