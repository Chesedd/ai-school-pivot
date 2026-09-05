"""Source contract for the canonical Informatics grades 7–9 curriculum."""
import json
from pathlib import Path

from app.infrastructure.models import normalize_catalog_name


DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
EXPECTED_TOPICS = {
    7: {"Цифровая грамотность", "Теоретические основы информатики",
        "Информационные технологии"},
    8: {"Теоретические основы информатики", "Алгоритмы и программирование"},
    9: {"Цифровая грамотность", "Теоретические основы информатики",
        "Алгоритмы и программирование", "Информационные технологии"},
}
EXPECTED_COUNTS = {7: (3, 130, 214), 8: (2, 82, 133), 9: (4, 125, 202)}
REPRESENTATIVE = {
    7: {
        "Цифровая грамотность": {"Файловая система"},
        "Теоретические основы информатики": {
            "Информационный объём данных", "Кодирование цвета"},
        "Информационные технологии": {
            "Текстовый процессор", "Растровая графика", "Мультимедийная презентация"},
    },
    8: {
        "Теоретические основы информатики": {
            "Двоичная система счисления", "Таблица истинности"},
        "Алгоритмы и программирование": {
            "Ветвление", "Цикл с условием", "Алгоритм Евклида", "Строковые данные"},
    },
    9: {
        "Цифровая грамотность": {"Информационная безопасность"},
        "Теоретические основы информатики": {"Граф", "Этапы компьютерного моделирования"},
        "Алгоритмы и программирование": {"Одномерный массив", "Обратная связь"},
        "Информационные технологии": {
            "Абсолютная адресация", "Численное моделирование в электронной таблице"},
    },
}


def _unique(values):
    normalized = [normalize_catalog_name(value) for value in values]
    assert len(normalized) == len(set(normalized))


def test_informatics_7_9_source_contract_and_normalized_uniqueness():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    matches = [subject for subject in source["subjects"] if subject["name"] == "Информатика"]
    assert len(matches) == 1
    grades = {grade["number"]: grade for grade in matches[0]["grades"]}
    assert set(grades) == {7, 8, 9}

    for number, expected_topics in EXPECTED_TOPICS.items():
        topics = grades[number]["topics"]
        assert {topic["name"] for topic in topics} == expected_topics
        assert (len(topics), sum(len(t["subtopics"]) for t in topics),
                sum(len(s["skills"]) for t in topics for s in t["subtopics"])) == \
            EXPECTED_COUNTS[number]
        _unique(topic["name"] for topic in topics)
        by_name = {topic["name"]: topic for topic in topics}
        for topic in topics:
            _unique(subtopic["name"] for subtopic in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                assert subtopic["skills"]
                _unique(subtopic["skills"])
        for topic_name, subtopic_names in REPRESENTATIVE[number].items():
            actual = {subtopic["name"] for subtopic in by_name[topic_name]["subtopics"]}
            assert subtopic_names <= actual


def test_informatics_boundaries_are_language_neutral_and_defensive():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    informatics = next(s for s in source["subjects"] if s["name"] == "Информатика")
    payload = json.dumps(informatics, ensure_ascii=False).casefold()
    for forbidden in (
        "python", "c++", "console.writeline", "range()", "огэ", "задание 15",
        "искусственный интеллект", "машинное обучение", "нейронные сети",
        "эксплуатация уязвимостей", "обход аутентификации", "подбор паролей",
    ):
        assert forbidden not in payload
    for required in ("фишинг", "безопасная аутентификация", "защита личной информации",
                     "сетевой этикет", "антивирусная защита"):
        assert required in payload
