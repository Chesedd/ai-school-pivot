"""Source contract for the canonical Informatics grades 10–11 curriculum."""
import hashlib
import json
from pathlib import Path

from app.infrastructure.models import normalize_catalog_name


DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
TOPICS = {
    10: {"Цифровая грамотность", "Теоретические основы информатики", "Информационные технологии"},
    11: {"Цифровая грамотность", "Теоретические основы информатики", "Алгоритмы и программирование", "Информационные технологии"},
}
COUNTS = {10: (3, 149, 161), 11: (4, 174, 181)}
PRESERVED_GRADES = {
    7: "284b2314a20306f9d9086e47403f05a36a67f8e97b0bba92398316146a91a11f",
    8: "2d0bd745dcd795ffeca3f8029c52a16434d553870c86a5fd9c4745d2d11c266b",
    9: "e4dab9955dc6471266527a2a8964d111742cf3f0fc9ed67155df5b902ec4fd25",
}
PRESERVED_SUBJECTS = {
    "Математика": "3bfcdbd2373bdffb9feee4810dcd35d60e29819e42a42e94c2aead0155542be0",
    "Русский язык": "b2525f3973d8419a62b6cfea00bd0b29930a0e99f95126937d91b55d0867046d",
    "Литература": "3d1582089264c3533b16a3b7d318e4eccb1918358198702507aef07a447de976",
    "Английский язык": "d0cb315ef79a24ef0f84a61881700e871d06d55d95b8bc1773b0f88a6eaff0e8",
    "Физика": "45fe16f4e562541673fcf4345fe4a5c4e1b2b99d82eabcc0621b6f7071240b64",
    "Химия": "ba5f442a840c94187772ff65cf8701daab474ca816a44366a82bc6458c785e59",
    "Биология": "c4a3091b9f6482be13a7afe625c2fd515af87943a342f6ce5b7f0dca72cb0d80",
    "История": "4cd375079401e1eaf43f58c5e0ea6f04e30aa0a09b09c190bc802a53923ac577",
    "Обществознание": "b3a260764ed523710befc363659b4f3e26e5b6c4bc06e46343e0ef41d298079e",
    "География": "92886886bcc9b0fcee03e4300047b868c85807bb3a90ca6fe1ee422400967932",
    "Литературное чтение": "12a198882f8c2132e0840219a0b2a8bb43f888488a07597610148b1efad30d4b",
    "Окружающий мир": "661beb23dc7b7bafd052b67a22c92517ba2dfd3d6e23e9efd6bc6b9979f8efa6",
}
REPRESENTATIVE = {
    10: {"Цифровая грамотность": {"Выбор конфигурации компьютера"}, "Теоретические основы информатики": {"Условие Фано", "Представление вещественных чисел в памяти компьютера", "Импликация", "Сумматор"}, "Информационные технологии": {"Библиографическая ссылка", "Трёхмерная модель"}},
    11: {"Цифровая грамотность": {"Доменное имя", "Резервное копирование"}, "Теоретические основы информатики": {"Оптимальный путь", "Выигрышная стратегия"}, "Алгоритмы и программирование": {"Второй максимум", "Подпрограммы"}, "Информационные технологии": {"Параметрический запрос", "Средства искусственного интеллекта", "Интернет вещей"}},
}


def fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def unique(values):
    normalized = [normalize_catalog_name(value) for value in values]
    assert len(normalized) == len(set(normalized))


def test_informatics_10_11_contract_uniqueness_and_preservation():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    matches = [subject for subject in source["subjects"] if subject["name"] == "Информатика"]
    assert len(matches) == 1
    grades = {grade["number"]: grade for grade in matches[0]["grades"]}
    assert set(grades) == {7, 8, 9, 10, 11}
    for number, digest in PRESERVED_GRADES.items():
        assert fingerprint(grades[number]) == digest
    for subject in source["subjects"]:
        if subject["name"] != "Информатика":
            assert fingerprint(subject) == PRESERVED_SUBJECTS[subject["name"]]
    for number in (10, 11):
        topics = grades[number]["topics"]
        assert {topic["name"] for topic in topics} == TOPICS[number]
        assert (len(topics), sum(len(t["subtopics"]) for t in topics), sum(len(s["skills"]) for t in topics for s in t["subtopics"])) == COUNTS[number]
        unique(t["name"] for t in topics)
        by_name = {t["name"]: t for t in topics}
        for topic in topics:
            unique(s["name"] for s in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                assert subtopic["skills"]
                unique(subtopic["skills"])
        for topic_name, expected in REPRESENTATIVE[number].items():
            assert expected <= {s["name"] for s in by_name[topic_name]["subtopics"]}


def test_senior_informatics_boundaries():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    informatics = next(s for s in source["subjects"] if s["name"] == "Информатика")
    grades = {g["number"]: g for g in informatics["grades"]}
    grade10 = json.dumps(grades[10], ensure_ascii=False).casefold()
    for forbidden in ("средства искусственного интеллекта", "параметрический запрос", "компьютерная сеть", "егэ", "задание 14"):
        assert forbidden not in grade10
    payload = json.dumps([grades[10], grades[11]], ensure_ascii=False).casefold()
    for forbidden in ("python", "console.writeline", "std::vector", "градиентный спуск", "backpropagation", "трансформеры", "эмбеддинги", "rag", "fine-tuning", "prompt engineering", "эксплуатация уязвимостей", "обход аутентификации", "взлом паролей", "создание вредоносного по"):
        assert forbidden not in payload
