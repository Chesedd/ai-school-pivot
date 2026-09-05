"""Contracts for the canonical Russian Language grades 7–9 source."""
import hashlib
import json
from pathlib import Path

from app.infrastructure.models import normalize_catalog_name

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
EXPECTED_TOPICS = {
    7: {"Общие сведения о языке", "Язык и речь", "Текст",
        "Функциональные разновидности языка", "Морфология",
        "Орфография и пунктуация"},
    8: {"Общие сведения о языке", "Язык и речь", "Текст",
        "Функциональные разновидности языка", "Синтаксис и пунктуация"},
    9: {"Общие сведения о языке", "Язык и речь", "Текст",
        "Функциональные разновидности языка", "Синтаксис и пунктуация"},
}
PRESERVED_RUSSIAN_FINGERPRINTS = {
    1: "3ed15bd27c8d6c8931bca4c875282aa067807b6fa3063b849645388bc1353c98",
    2: "85f5ce177949b63f648cd9466013e654968af935f6d17b20bf7aa380f4254a87",
    3: "39d14547fe39e912948ad3fba57ba0128287c657d5c18ca907b9506f5ce56c8d",
    4: "ab9350654a9d35d15a5c7eb4d40176649b21dbaf7df2353abdfca472ee591aa2",
    5: "6f6d3f7ef6be2e542c5f77c7e9ec93ea10258ccc45a709462c042973d572bd32",
    6: "b2e3c1320ac26624a65b11eb3ff7708d938d2a568f07fbd9774b47026bd51acb",
}
PRESERVED_OTHER_SUBJECT_FINGERPRINTS = {
    "Математика": "1702f86692e556e5a92d148358aca8911124f77c5f8d955d35c88d284f41e66a",
    "Литература": "fbe9eb1fb3efe4c2ee98dda56104f9e329559e12ff7b426502a44ff83b138487",
    "Английский язык": "f78ff1c654a28495a4dd942cf96c4517a06f74dd1a14171d7fe07f31b31e5914",
    "Информатика": "4e3ae9a1e3d54f927d5ebddc0762ba63d8b769982fbdb5be1e920924aad33d77",
    "Физика": "f855c4eb243a8e6ddd58bf794179ca0bfa0fcdc7409d4125fc69a3ad0d379927",
    "Химия": "01d1839896c637d7ff1909025ac10d565019b7030e20f4363fc7e2f242a716b4",
    "Биология": "e18d814a73c1f95e35d3f3ff573c237d4f2f55214c21a2da49c3799a1dbf1027",
    "История": "2dfb745d109356d5e3a386bbed384b88437687b7097f5f854d4d581b7eddd95f",
    "Обществознание": "1f4a74c204823c1c1efb26f714d6a49eff0c0dee36c3b7b1d2d241721e54b038",
    "География": "4f851a42d3ff3958b392c55321cc45cfba747201913d5765d6f216da9a42fbd0",
    "Литературное чтение": "f33f125b5d6cd89cf90312676784a5344f9ade2d49e3b15533bf44df36e0a23d",
    "Окружающий мир": "ac0c6bab8624fd73eb849af43fb597b64c00e6856e1523d472ef9dd24e4a74cd",
}


def _source():
    return json.loads(DATA.read_text(encoding="utf-8"))


def _fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _unique(names):
    normalized = [normalize_catalog_name(name) for name in names]
    assert len(normalized) == len(set(normalized))


def test_source_shape_topics_counts_and_normalized_uniqueness():
    subjects = [s for s in _source()["subjects"] if s["name"] == "Русский язык"]
    assert len(subjects) == 1
    grades = {g["number"]: g for g in subjects[0]["grades"]}
    assert set(grades) == set(range(1, 10))
    for number in (7, 8, 9):
        topics = grades[number]["topics"]
        assert {t["name"] for t in topics} == EXPECTED_TOPICS[number]
        _unique(t["name"] for t in topics)
        for topic in topics:
            _unique(s["name"] for s in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                assert subtopic["skills"]
                _unique(subtopic["skills"])


def test_previous_russian_grades_and_every_other_subject_are_unchanged():
    source = _source()
    russian = next(s for s in source["subjects"] if s["name"] == "Русский язык")
    grades = {g["number"]: g for g in russian["grades"]}
    assert {n: _fingerprint(grades[n]) for n in range(1, 7)} == PRESERVED_RUSSIAN_FINGERPRINTS
    others = {s["name"]: _fingerprint(s) for s in source["subjects"]
              if s["name"] != "Русский язык"}
    assert others == PRESERVED_OTHER_SUBJECT_FINGERPRINTS


def test_historical_grade_7_nodes_are_preserved_exactly():
    russian = next(s for s in _source()["subjects"] if s["name"] == "Русский язык")
    grade = next(g for g in russian["grades"] if g["number"] == 7)
    morphology = next(t for t in grade["topics"] if t["name"] == "Морфология")
    participle = next(s for s in morphology["subtopics"] if s["name"] == "Причастие")
    assert {"Находить причастия в тексте", "Выполнять морфологический разбор"} <= set(participle["skills"])


def test_representative_nodes_are_present():
    russian = next(s for s in _source()["subjects"] if s["name"] == "Русский язык")
    grades = {g["number"]: g for g in russian["grades"]}
    expected = {
        7: {"Морфология": {"Причастие", "Деепричастие", "Наречие", "Предлог", "Союз", "Частица"}},
        8: {"Синтаксис и пунктуация": {"Согласование", "Составное именное сказуемое",
             "Односоставные предложения", "Обособленные определения", "Вводные конструкции"}},
        9: {"Синтаксис и пунктуация": {"Сложносочинённое предложение",
             "Сложноподчинённое предложение", "Придаточное определительное",
             "Бессоюзное сложное предложение", "Сложное предложение с разными видами связи",
             "Цитирование"}},
    }
    for number, by_topic in expected.items():
        topics = {t["name"]: t for t in grades[number]["topics"]}
        for topic_name, names in by_topic.items():
            assert names <= {s["name"] for s in topics[topic_name]["subtopics"]}


def test_grade_boundaries_literature_and_oge_numbering_are_respected():
    russian = next(s for s in _source()["subjects"] if s["name"] == "Русский язык")
    grades = {g["number"]: g for g in russian["grades"]}
    names = {n: {x for t in grades[n]["topics"] for st in t["subtopics"]
                 for x in (t["name"], st["name"], *st["skills"])} for n in (7, 8, 9)}
    assert not any("односостав" in x.casefold() for x in names[7])
    assert not any("бессоюз" in x.casefold() for x in names[8])
    assert not any("дееприч" in x.casefold() for x in names[9])
    forbidden = ("анализ произведения", "тема произведения", "идея произведения", "сюжет",
                 "литературный герой", "характеристика героя", "авторская позиция",
                 "жанр произведения", "огэ", "задание 2", "задание 3", "задание 4")
    assert not any(term in x.casefold() for names_for_grade in names.values()
                   for x in names_for_grade for term in forbidden)
