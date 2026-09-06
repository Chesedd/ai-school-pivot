"""Source contract for the canonical Physics grades 10–11 curriculum."""
import hashlib
import json
from pathlib import Path

from app.infrastructure.models import normalize_catalog_name

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
TOPICS = {
    10: {"Физика и методы научного познания", "Механика", "Молекулярная физика и термодинамика", "Электродинамика"},
    11: {"Электродинамика", "Колебания и волны", "Основы специальной теории относительности", "Квантовая физика", "Элементы астрономии и астрофизики"},
}
FROZEN_PHYSICS_7_9 = {
    7: "ba26b648ad3b418f0b45ccd45ccfa7e9da6fefa84a1ba6e949c06f10007b9c26",
    8: "baade46ae874af0e1c62dfe10e9d21afeac1e58701d35c83fd21d187f0384096",
    9: "15d446ea4a81e19dde1351243970b0e6dd8eaad5edec9f030d09afc5e141fb98",
}
FROZEN_OTHER_SUBJECTS = "2bb91d2aeaa85a4e1846d87520ffaea50de9f6d1d03ebd6de4fb215f416abc82"
REPRESENTATIVE = {
    10: {
        "Физика и методы научного познания": {"Принцип соответствия"},
        "Механика": {"Мгновенная скорость", "Второй закон Ньютона", "Закон сохранения импульса"},
        "Молекулярная физика и термодинамика": {"Уравнение Менделеева–Клапейрона", "Первый закон термодинамики", "Цикл Карно", "Насыщенный пар"},
        "Электродинамика": {"Электроёмкость плоского конденсатора", "Закон Ома для полной цепи", "p–n-переход"},
    },
    11: {
        "Электродинамика": {"Сила Ампера", "Сила Лоренца", "Закон электромагнитной индукции Фарадея", "Самоиндукция"},
        "Колебания и волны": {"Формула Томсона", "Трансформатор", "Интерференция света", "Формула тонкой линзы"},
        "Основы специальной теории относительности": {"Замедление времени"},
        "Квантовая физика": {"Уравнение Эйнштейна для фотоэффекта", "Волны де Бройля", "Закон радиоактивного распада"},
        "Элементы астрономии и астрофизики": {"Закон Хаббла", "Реликтовое излучение"},
    },
}


def fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def assert_unique(values):
    normalized = [normalize_catalog_name(value) for value in values]
    assert len(normalized) == len(set(normalized))


def test_physics_10_11_source_contract_and_preservation():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    matches = [subject for subject in source["subjects"] if subject["name"] == "Физика"]
    assert len(matches) == 1
    grades = {grade["number"]: grade for grade in matches[0]["grades"]}
    assert set(grades) == {7, 8, 9, 10, 11}
    assert {number: fingerprint(grades[number]) for number in (7, 8, 9)} == FROZEN_PHYSICS_7_9
    assert fingerprint([s for s in source["subjects"] if s["name"] != "Физика"]) == FROZEN_OTHER_SUBJECTS
    for number in (10, 11):
        topics = grades[number]["topics"]
        assert {topic["name"] for topic in topics} == TOPICS[number]
        assert_unique(topic["name"] for topic in topics)
        by_name = {topic["name"]: topic for topic in topics}
        for topic in topics:
            assert_unique(subtopic["name"] for subtopic in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                assert subtopic["skills"]
                assert_unique(subtopic["skills"])
        for topic_name, expected in REPRESENTATIVE[number].items():
            assert expected <= {s["name"] for s in by_name[topic_name]["subtopics"]}


def test_grade_boundaries_and_non_curriculum_modules_are_excluded():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    physics = next(s for s in source["subjects"] if s["name"] == "Физика")
    grades = {g["number"]: json.dumps(g, ensure_ascii=False).casefold() for g in physics["grades"]}
    assert all(term not in grades[10] for term in ("сила ампера", "сила лоренца", "фотоэффект", "закон хаббла", "формула томсона"))
    assert all(term not in grades[11] for term in ("уравнение менделеева–клапейрона", "закон ома для полной цепи"))
    forbidden = ("подготовка к егэ", "задание 4 егэ", "обобщающее повторение", "лабораторная работа №")
    assert all(term not in grades[10] + grades[11] for term in forbidden)
